"""
GFlowNet 核心 — 轨迹采样 + Trajectory Balance / Sub-trajectory Balance 损失

前向采样:
  1. 从 BOS 状态开始 (stack_depth=1, token_ids=[BOS])
  2. 每步: 编码状态 → 获取合法动作 → 前向策略采样 → 添加到序列
  3. 直到 STOP 或 max_len → 返回轨迹

Sub-trajectory Balance (v2.2):
  对于每条轨迹，在所有前缀对 (i,j) 上计算 TB 式损失:
    L(i,j) = (Σlog_pf[i:j] + log_F(s_j) - log_F(s_i) - Σlog_pb[i:j])²
  边界条件: F(s_0) = logZ, F(s_T) = log_R
  权重: λ^(j-i-1) — λ 越大，越关注短前缀

Trajectory Balance (v2.0, 保留为 fallback):
  L(τ) = (logZ + ΣlogP_F - logR - ΣlogP_B)²
"""

import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Tuple, Optional

from .config import GFNConfig
from .grammar import (
    N_ACTIONS, VOCAB_SIZE,
    BOS_IDX, EOS_IDX, STOP_IDX, PAD_IDX,
    ACTION_IDX_TO_TOKEN, TOKEN_IDX_TO_ACTION,
    get_valid_actions, update_stack_depth,
    validate_and_build, sequence_to_formula,
)
from .encoder import FactorStateEncoder
from .policy import ForwardPolicy, UniformBackwardPolicy


class StateFlowPredictor(nn.Module):
    """
    状态流预测器 F(s) → log_F(s)。

    用于 Sub-trajectory Balance 损失:
      L(i,j) = (Σlog_pf[i:j] + log_F(s_j) - log_F(s_i) - Σlog_pb[i:j])²

    边界条件由外部设置: F(s_0)=logZ, F(s_T)=log_R。
    中间状态的 F(s) 由网络预测。

    架构: 3 层 MLP over state vector
    """

    def __init__(self, state_dim: int, hidden_dim: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, states: torch.Tensor) -> torch.Tensor:
        """
        参数:
            states: (*, state_dim) — batch of state vectors
        返回:
            log_F: (*,) — predicted log state flow
        """
        return self.net(states).squeeze(-1)


class GFlowNetCore(nn.Module):
    """
    GFlowNet 核心模块: 编码器 + 前向策略 + 后向策略 + 流预测器 + logZ
    """

    def __init__(self, config: GFNConfig):
        super().__init__()

        self.config = config
        self.n_actions = N_ACTIONS
        self.max_len = config.max_expr_len

        # Encoder
        self.encoder = FactorStateEncoder(config)
        state_dim = self.encoder.get_output_dim()

        # Forward policy
        self.forward_policy = ForwardPolicy(config, state_dim)

        # Backward policy (uniform for MVP)
        self.backward_policy = UniformBackwardPolicy()

        # State flow predictor (for SubTB loss)
        self.flow_predictor = StateFlowPredictor(state_dim)

        # Learnable logZ
        self.logZ = nn.Parameter(torch.tensor(
            config.log_z_init if hasattr(config, 'log_z_init') else 0.0
        ))

    def encode_state(
        self,
        token_ids: torch.Tensor,
        stack_depth: int,
    ) -> torch.Tensor:
        """
        编码单个状态。

        参数:
            token_ids:  (1, seq_len) — 当前 token 序列 (含 BOS)
            stack_depth: int — 当前栈深度

        返回:
            state: (1, state_dim)
        """
        sd = torch.tensor([stack_depth], dtype=torch.long)
        result = self.encoder(token_ids, sd)
        return result["pooled"]  # (1, state_dim)

    def sample_trajectory(
        self,
        temperature: float = 1.0,
    ) -> Tuple[List[int], List[int], List[float], List[int], Optional[str]]:
        """
        前向采样一条完整轨迹。

        参数:
            temperature: 采样温度 (>1 = 更多样)

        返回:
            (token_ids, action_indices, log_pfs, stack_depths, formula_or_None)
              - token_ids:     完整的 token 序列 (含 BOS), 不包括 EOS
              - action_indices: 每步选择的 action index (0..N_ACTIONS-1)
              - log_pfs:       每步的 log P_F
              - stack_depths:  每步之前的栈深度 (len = T, 对应转移前状态)
              - formula:       重建的公式字符串 (如果成功), 否则 None
        """
        token_ids = [BOS_IDX]
        action_indices = []
        log_pfs = []
        stack_depths = []  # 记录每步转移前的深度
        stack_depth = 1  # BOS.arity = 1

        for step in range(self.max_len):
            # Record pre-transition stack depth
            stack_depths.append(stack_depth)

            # Encode current state
            tokens_t = torch.tensor([token_ids], dtype=torch.long)  # (1, seq_len)
            sd_t = torch.tensor([stack_depth], dtype=torch.long)     # (1,)
            enc = self.encoder(tokens_t, sd_t)
            state = enc["pooled"]  # (1, state_dim)

            # Get valid actions
            valid_token_ids = get_valid_actions(stack_depth, step, self.max_len)
            if not valid_token_ids:
                break

            # Convert to action indices and mask
            action_indices_for_valid = []
            mask = torch.zeros(1, self.n_actions, dtype=torch.bool)
            for tid in valid_token_ids:
                aid = TOKEN_IDX_TO_ACTION.get(tid)
                if aid is not None:
                    action_indices_for_valid.append(aid)
                    mask[0, aid] = True

            if not action_indices_for_valid:
                break

            # Sample action
            action, log_pf = self.forward_policy.sample(state, mask, temperature)
            action_idx = action.item()
            log_pf_val = log_pf.item()

            # Convert action_idx back to token_idx
            token_idx = ACTION_IDX_TO_TOKEN[action_idx]

            # STOP → end trajectory
            if token_idx == STOP_IDX:
                action_indices.append(action_idx)
                log_pfs.append(log_pf_val)
                break

            # Update state
            stack_depth = update_stack_depth(stack_depth, token_idx)
            token_ids.append(token_idx)
            action_indices.append(action_idx)
            log_pfs.append(log_pf_val)

        # Build formula
        formula = None
        expr_tokens = token_ids[1:]  # exclude BOS
        if expr_tokens:
            valid, result = validate_and_build(expr_tokens)
            if valid:
                formula = result

        return token_ids, action_indices, log_pfs, stack_depths, formula

    def sample_batch(
        self,
        batch_size: int,
        temperature: float = 1.0,
    ) -> Tuple[List[List[int]], List[List[int]], List[List[float]], List[List[int]], List[Optional[str]]]:
        """
        批量采样轨迹。

        返回:
            (token_ids_batch, action_indices_batch, log_pfs_batch, stack_depths_batch, formulas)
        """
        tids_batch = []
        aids_batch = []
        lpfs_batch = []
        sds_batch = []
        formulas = []

        for _ in range(batch_size):
            tids, aids, lpfs, sds, formula = self.sample_trajectory(temperature)
            tids_batch.append(tids)
            aids_batch.append(aids)
            lpfs_batch.append(lpfs)
            sds_batch.append(sds)
            formulas.append(formula)

        return tids_batch, aids_batch, lpfs_batch, sds_batch, formulas

    def _encode_prefix_states(
        self,
        token_ids_batch: List[List[int]],
        stack_depths_batch: List[List[int]],
    ) -> Tuple[List[torch.Tensor], torch.Tensor]:
        """
        批量编码所有轨迹的所有前缀状态。

        参数:
            token_ids_batch:  每条轨迹的 token 序列 [BOS, t1, t2, ...]
            stack_depths_batch: 每条轨迹每步前的栈深度

        返回:
            (prefix_states_per_traj, flat_log_F)
              prefix_states_per_traj[i]: (T_i + 1, state_dim) — 包括初始状态
              flat_log_F: (total_states,) — 所有状态的 log_F（初始状态用 logZ）
        """
        device = self.logZ.device

        # 收集所有前缀的 (token_ids, stack_depth) 对
        all_prefix_tokens = []  # list of tensors (1, prefix_len)
        all_stack_depths = []   # list of ints
        per_traj_state_counts = []  # how many states per trajectory

        for tids, sds in zip(token_ids_batch, stack_depths_batch):
            T = len(sds)  # number of transitions
            # States: s_0 (after BOS), s_1, ..., s_T
            n_states = T + 1
            per_traj_state_counts.append(n_states)

            for t in range(n_states):
                prefix = tids[:t + 1]  # first t+1 tokens (BOS + t actions)
                all_prefix_tokens.append(torch.tensor([prefix], dtype=torch.long))
                if t < T:
                    all_stack_depths.append(sds[t])  # pre-transition depth
                else:
                    # Terminal state: stack_depth = 0 (expression complete)
                    all_stack_depths.append(0)

        if not all_prefix_tokens:
            return [], torch.tensor([], device=device)

        # Pad and batch-encode all prefix states
        max_len = max(t.shape[1] for t in all_prefix_tokens)
        padded = torch.zeros(len(all_prefix_tokens), max_len, dtype=torch.long)
        for i, t in enumerate(all_prefix_tokens):
            padded[i, :t.shape[1]] = t

        sd_tensor = torch.tensor(all_stack_depths, dtype=torch.long)

        # Move to device
        padded = padded.to(device)
        sd_tensor = sd_tensor.to(device)

        # Batch encode
        enc_result = self.encoder(padded, sd_tensor)
        all_states = enc_result["pooled"]  # (total_states, state_dim)

        # Compute log_F for all states
        log_F_all = self.flow_predictor(all_states)  # (total_states,)

        # Split back per trajectory
        prefix_states = []
        offset = 0
        for count in per_traj_state_counts:
            prefix_states.append(all_states[offset:offset + count])
            offset += count

        return prefix_states, log_F_all

    def compute_subtb_loss(
        self,
        token_ids_batch: List[List[int]],
        stack_depths_batch: List[List[int]],
        log_pfs_batch: List[List[float]],
        log_rewards: torch.Tensor,
        lambda_: float = 0.9,
    ) -> torch.Tensor:
        """
        计算 Sub-trajectory Balance 损失。

        对每条轨迹的每个前缀对 (i,j) (0 ≤ i < j ≤ T):
            pf_sum = Σ_{k=i}^{j-1} log_pf[k]
            pb_sum = (j-i) * log_uniform  (均匀后向)
            delta = pf_sum + log_F(s_j) - log_F(s_i) - pb_sum
            weight = λ^(j-i-1)
            loss += weight * 0.5 * delta^2

        边界条件:
            log_F(s_0) = logZ  (初始状态流 = 分区函数)
            log_F(s_T) = log_R   (终止状态流 = 奖励)

        参数:
            token_ids_batch:    每条轨迹的 token 序列
            stack_depths_batch: 每条轨迹每步前的栈深度
            log_pfs_batch:      每条轨迹每步的 log P_F
            log_rewards:        (batch_size,) — log(R + ε)
            lambda_:            SubTB λ 参数

        返回:
            scalar loss (归一化)
        """
        device = self.logZ.device
        batch_size = len(token_ids_batch)

        # Encode all prefix states + compute log_F
        prefix_states, log_F_flat = self._encode_prefix_states(
            token_ids_batch, stack_depths_batch
        )

        total_loss = torch.tensor(0.0, device=device)
        total_weight = torch.tensor(0.0, device=device)

        log_pb_per_step = self.backward_policy.log_uniform  # constant per step

        for i in range(batch_size):
            T = len(log_pfs_batch[i])  # number of transitions
            if T == 0:
                continue

            # Build cumulative sums for fast prefix computation
            # pf_cumsum[k] = sum of log_pf[0..k-1]
            pf_cumsum = torch.tensor([0.0] + log_pfs_batch[i], device=device).cumsum(dim=0)
            # pf_cumsum is length T+1: [0, pf[0], pf[0]+pf[1], ..., sum]

            for i_idx in range(T + 1):
                for j_idx in range(i_idx + 1, T + 1):
                    pf_sum = pf_cumsum[j_idx] - pf_cumsum[i_idx]
                    pb_sum = (j_idx - i_idx) * log_pb_per_step

                    # log_F for s_i and s_j
                    log_F_i = self.logZ if i_idx == 0 else log_F_flat[
                        sum(len(token_ids_batch[k]) for k in range(i)) + i_idx
                    ]
                    log_F_j = log_rewards[i] if j_idx == T else log_F_flat[
                        sum(len(token_ids_batch[k]) for k in range(i)) + j_idx
                    ]

                    delta = pf_sum + log_F_j - log_F_i - pb_sum
                    weight = lambda_ ** (j_idx - i_idx - 1)

                    total_loss = total_loss + weight * 0.5 * delta ** 2
                    total_weight = total_weight + weight

        if total_weight > 0:
            return total_loss / total_weight
        return total_loss

    def compute_tb_loss(
        self,
        log_pf_sums: List[float],
        num_transitions_list: List[int],
        log_rewards: torch.Tensor,
    ) -> torch.Tensor:
        """
        计算 Trajectory Balance 损失 (保留用于 fallback)。

        参数:
            log_pf_sums:          每条轨迹的 Σ log P_F
            num_transitions_list: 每条轨迹的步数
            log_rewards:          每条轨迹的 log(R + ε) — (batch_size,)

        返回:
            scalar loss
        """
        batch_size = len(log_pf_sums)
        device = self.logZ.device
        total_loss = torch.tensor(0.0, device=device)

        for i in range(batch_size):
            log_pf_sum = log_pf_sums[i]
            log_pb_sum = self.compute_log_pb_sum(num_transitions_list[i])
            log_R = log_rewards[i]

            # TB: (logZ + log_pf - log_R - log_pb)²
            delta = self.logZ + log_pf_sum - log_R - log_pb_sum
            total_loss = total_loss + 0.5 * delta ** 2

        return total_loss / batch_size

    def compute_log_pf_sum(self, log_pfs: List[float]) -> torch.Tensor:
        """计算轨迹的 log P_F 总和"""
        if not log_pfs:
            return torch.tensor(0.0)
        return torch.tensor(sum(log_pfs), dtype=torch.float32)

    def compute_log_pb_sum(self, num_transitions: int) -> torch.Tensor:
        """计算轨迹的 log P_B 总和 (均匀后向策略)"""
        return self.backward_policy.log_prob_tensor(num_transitions)

    def compute_tb_loss_vectorized(
        self,
        log_pf_sums: List[float],
        num_transitions_list: List[int],
        log_rewards: torch.Tensor,
    ) -> torch.Tensor:
        """
        向量化的 TB 损失计算 (用于更大批量, 保留用于 fallback)。

        参数同 compute_tb_loss。
        """
        log_pf_sum_t = torch.tensor(log_pf_sums, dtype=torch.float32)
        log_pb_sum_t = torch.tensor(
            [self.backward_policy.log_prob(n) for n in num_transitions_list],
            dtype=torch.float32,
        )

        delta = self.logZ + log_pf_sum_t - log_rewards - log_pb_sum_t
        return 0.5 * (delta ** 2).mean()

    def get_action_distribution(
        self,
        token_ids: List[int],
        stack_depth: int,
        temperature: float = 1.0,
    ) -> Optional[torch.Tensor]:
        """
        获取当前状态下的动作概率分布 (用于分析)。

        参数:
            token_ids:  当前序列 (含 BOS)
            stack_depth: 栈深度
            temperature: 温度

        返回:
            (N_ACTIONS,) 概率分布，或 None
        """
        step = len(token_ids) - 1  # exclude BOS

        tokens_t = torch.tensor([token_ids], dtype=torch.long)
        sd_t = torch.tensor([stack_depth], dtype=torch.long)
        enc = self.encoder(tokens_t, sd_t)
        state = enc["pooled"]

        valid_token_ids = get_valid_actions(stack_depth, step, self.max_len)
        mask = torch.zeros(1, self.n_actions, dtype=torch.bool)
        for tid in valid_token_ids:
            aid = TOKEN_IDX_TO_ACTION.get(tid)
            if aid is not None:
                mask[0, aid] = True

        if not mask.any():
            return None

        return self.forward_policy.get_probs(state, mask, temperature).squeeze(0)
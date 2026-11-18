"""
GFlowNet 策略网络 — 前向策略 + 后向策略

前向策略 (Forward Policy):
  输入: 状态向量 (d_model + stack_embed_dim)
  输出: action 概率分布 (N_ACTIONS)

后向策略 (Backward Policy — MVP: 均匀分布):
  P_B = 1 / |valid_actions_in_parent|  (均匀)
"""

import math
import torch
import torch.nn as nn
import torch.nn.functional as F

from .config import GFNConfig
from .grammar import N_ACTIONS


class ForwardPolicy(nn.Module):
    """
    前向策略 P_F(action | state)。

    架构: MLP over state embedding → action logits → masked softmax → probs
    """

    def __init__(self, config: GFNConfig, state_dim: int):
        super().__init__()

        self.state_dim = state_dim
        self.n_actions = N_ACTIONS

        self.net = nn.Sequential(
            nn.Linear(state_dim, 128),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(128, 64),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(64, N_ACTIONS),
        )

    def forward(self, state: torch.Tensor) -> torch.Tensor:
        """
        返回原始 logits (未 mask, 未 softmax)。

        参数:
            state: (batch, state_dim)
        返回:
            logits: (batch, N_ACTIONS)
        """
        return self.net(state)

    def get_probs(
        self,
        state: torch.Tensor,
        valid_mask: torch.Tensor,
        temperature: float = 1.0,
    ) -> torch.Tensor:
        """
        返回 masked + temperature-scaled softmax 概率。

        参数:
            state:       (batch, state_dim)
            valid_mask:  (batch, N_ACTIONS) — bool tensor, True=合法
            temperature: 采样温度

        返回:
            probs: (batch, N_ACTIONS) — 对每行的合法 action 求和为 1
        """
        logits = self.forward(state) / temperature

        # Mask invalid actions
        logits = logits.masked_fill(~valid_mask, -float("inf"))

        # Softmax
        return F.softmax(logits, dim=-1)

    def get_log_probs(
        self,
        state: torch.Tensor,
        valid_mask: torch.Tensor,
    ) -> torch.Tensor:
        """
        返回合法 action 的 log probability。

        参数:
            state:       (batch, state_dim)
            valid_mask:  (batch, N_ACTIONS)
        返回:
            log_probs: (batch, N_ACTIONS) — 非法 action 为 -inf
        """
        logits = self.forward(state)
        logits = logits.masked_fill(~valid_mask, -float("inf"))
        return F.log_softmax(logits, dim=-1)

    def sample(
        self,
        state: torch.Tensor,
        valid_mask: torch.Tensor,
        temperature: float = 1.0,
    ) -> tuple:
        """
        从策略中采样一个 action。

        参数:
            state:       (batch, state_dim)
            valid_mask:  (batch, N_ACTIONS)
            temperature: 采样温度

        返回:
            (actions, log_probs)
              actions:    (batch,) — 采样的 action index (0..N_ACTIONS-1)
              log_probs:  (batch,) — 所选 action 的 log probability
        """
        probs = self.get_probs(state, valid_mask, temperature)

        # Categorical sampling
        dist = torch.distributions.Categorical(probs)
        actions = dist.sample()  # (batch,)

        # Log prob of chosen actions
        log_probs = dist.log_prob(actions)  # (batch,)

        return actions, log_probs

    def get_action_log_prob(
        self,
        state: torch.Tensor,
        valid_mask: torch.Tensor,
        action: torch.Tensor,
    ) -> torch.Tensor:
        """
        计算给定 action 的 log probability。

        参数:
            state:       (batch, state_dim)
            valid_mask:  (batch, N_ACTIONS)
            action:      (batch,) — action indices

        返回:
            log_probs: (batch,)
        """
        log_probs = self.get_log_probs(state, valid_mask)  # (batch, N_ACTIONS)
        return log_probs.gather(1, action.unsqueeze(1)).squeeze(1)


class UniformBackwardPolicy:
    """
    均匀后向策略 (MVP 简化)。

    P_B(parent_action | child_state) = 1 / |vocab|

    log_pb = -log(N_ACTIONS) for every transition.

    在 TB 损失中，log_pb 部分退化为常量:
      sum(log_pb) = -len(traj) * log(N_ACTIONS)
    """

    def __init__(self):
        self.log_uniform = -math.log(N_ACTIONS)

    def log_prob(self, num_transitions: int) -> float:
        """
        返回轨迹的 log_pb 总和。

        参数:
            num_transitions: 轨迹中的转移步数
        """
        return num_transitions * self.log_uniform

    def log_prob_tensor(self, num_transitions: int) -> torch.Tensor:
        """返回 tensor 形式（用于梯度计算——虽然是常量，但保持类型一致）"""
        return torch.tensor(num_transitions * self.log_uniform, dtype=torch.float32)
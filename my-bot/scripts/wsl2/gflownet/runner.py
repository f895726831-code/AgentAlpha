"""
GFlowNet 训练循环 — 采样→评估→SubTB 损失→梯度累积→更新

v2.2 改进:
  - SubTB loss: 对所有轨迹前缀提供学习信号 (替代仅完整轨迹的 TB)
  - 梯度累积: 多个 micro-batch 累积后更新 (effective batch = batch_size × grad_accum_steps)
  - 流预测器: 独立学习率的状态流网络

训练流程:
  每个 episode:
    1. 梯度累积循环 (grad_accum_steps 次):
        a. 采样 micro-batch (batch_size 条轨迹)
        b. 公式重建 + 奖励计算 (FFO)
        c. SubTB 损失计算 (所有前缀对)
        d. loss.backward() (累积)
    2. 梯度裁剪 + 参数更新
    3. 定期日志 + 检查点保存
"""

import os
import math
import time
import json
import torch
import torch.optim as optim
from typing import List, Optional, Dict
from datetime import datetime

from .config import GFNConfig
from .gfn import GFlowNetCore, StateFlowPredictor
from .reward import RewardComputer
from .eval import MultiObjectiveEvaluator
from .sampler import DualChannelSampler


class GFlowNetRunner:
    """
    GFlowNet 训练器 (v2.2)。

    使用:
        config = GFNConfig()
        runner = GFlowNetRunner(config)
        runner.train()
        runner.save_checkpoint("model.pt")
    """

    def __init__(self, config: GFNConfig = None, data_dir: str = None):
        self.config = config or GFNConfig()
        self.data_dir = data_dir

        # Wire grammar constraint from config
        from . import grammar as _grammar
        _grammar.ALLOW_COMPARISON_OPS = self.config.allow_comparison_ops

        # 模型
        self.model = GFlowNetCore(self.config)

        # 优化器: 策略参数 + 编码器参数 + 流预测器参数
        policy_params = list(self.model.encoder.parameters()) + list(
            self.model.forward_policy.parameters()
        )
        self.optimizer = optim.AdamW(
            policy_params,
            lr=self.config.lr,
            weight_decay=self.config.weight_decay,
        )

        # logZ 独立优化器 (更大学习率)
        self.optimizer_logZ = optim.AdamW(
            [self.model.logZ],
            lr=self.config.lr_logZ,
        )

        # 流预测器独立优化器
        self.optimizer_flow = optim.AdamW(
            self.model.flow_predictor.parameters(),
            lr=self.config.lr_flow,
        )

        # 学习率调度器
        self.scheduler = None
        self.scheduler_logZ = None
        self.scheduler_flow = None

        # 奖励计算器 (延迟初始化)
        self.reward_computer: Optional[RewardComputer] = None

        # v4 多目标评价器
        self.evaluator = MultiObjectiveEvaluator()

        # v4 模式标志
        self._use_v4 = getattr(self.config, 'reward_mode', 'v4') == 'v4'

        # v4 双通道采样器
        self._sampler = None
        if getattr(self.config, 'sampler_mode', 'standard') == 'dual_channel':
            self._sampler = DualChannelSampler(
                exploit_ratio=getattr(self.config, 'sampler_exploit_ratio', 0.6),
                exploit_temp=getattr(self.config, 'sampler_exploit_temp', 0.5),
                explore_temp=getattr(self.config, 'sampler_explore_temp', 1.5),
            )

        # 检查点恢复的因子池（在 train() 首次创建 reward_computer 时恢复）
        self._saved_factor_pool: List = []

        # 训练统计
        self.episode = 0
        self.metrics_history: List[Dict] = []
        self.best_loss = float("inf")

    def _restore_checkpoint_pool(self, verbose: bool = True):
        """恢复检查点的因子池 + 重建去重索引"""
        saved_pool = getattr(self, '_saved_factor_pool', None)
        if saved_pool:
            for formula, ic in saved_pool:
                self.reward_computer.factor_pool.append((formula, float(ic)))
            # Rebuild AST hashes from restored pool (v2.1 dedup fix)
            self.reward_computer.rebuild_pool_hashes()
            if verbose:
                print(f"[Reward] Restored {len(saved_pool)} factors from checkpoint pool")
            self._saved_factor_pool = []

    def train(self, n_episodes: int = None, verbose: bool = True):
        """
        训练 GFlowNet (v2.2: SubTB + 梯度累积)。

        参数:
            n_episodes: 训练 episode 数 (默认使用 config)
            verbose:    是否输出日志
        """
        if n_episodes is None:
            n_episodes = self.config.n_episodes

        cfg = self.config

        # 延迟初始化奖励计算器 (首次训练时加载数据)
        if self.reward_computer is None:
            if verbose:
                print("[Reward] Loading Qlib data (one-time)...")
            t0 = time.time()
            self.reward_computer = RewardComputer(cfg, self.data_dir)
            if verbose:
                print(f"[Reward] Data loaded in {time.time() - t0:.1f}s")
            self._restore_checkpoint_pool(verbose)

        # 学习率调度
        self.scheduler = optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer, T_max=n_episodes, eta_min=cfg.lr / 100,
        )
        self.scheduler_logZ = optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer_logZ, T_max=n_episodes, eta_min=cfg.lr_logZ / 100,
        )
        self.scheduler_flow = optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer_flow, T_max=n_episodes, eta_min=cfg.lr_flow / 100,
        )

        # 预热
        warmup_done = False
        accum_steps = getattr(cfg, 'grad_accum_steps', 1)
        subtb_lambda = getattr(cfg, 'subtb_lambda', 0.9)

        if verbose:
            print(f"[Train] Starting {n_episodes} episodes, "
                  f"micro_batch={cfg.batch_size}, grad_accum={accum_steps} "
                  f"(effective batch={cfg.batch_size * accum_steps})")
            print(f"[Train] Loss: SubTB(lambda={subtb_lambda})")
            print(f"[Train] Eval period: {cfg.eval_start} -> {cfg.eval_end} ({cfg.eval_universe})")
            if self._sampler:
                print(f"[Train] Sampler: DualChannel "
                      f"(exploit={self._sampler.exploit_ratio:.0%} t={self._sampler.exploit_temp}, "
                      f"explore t={self._sampler.explore_temp})")
            print(f"[Train] Initial logZ: {self.model.logZ.item():.4f}")
            print()

        t_start = time.time()

        for ep in range(n_episodes):
            self.episode = ep

            # ─── 预热学习率 ───
            if ep > 0 and ep <= cfg.warmup_episodes and not warmup_done:
                lr_scale = ep / cfg.warmup_episodes
                for pg in self.optimizer.param_groups:
                    pg["lr"] = cfg.lr * lr_scale

            # ─── 梯度累积循环 ───
            all_rewards = []
            all_valid_formulas = []

            for accum_step in range(accum_steps):
                # Phase 1: 采样 micro-batch (support dual-channel)
                if self._sampler:
                    tids_batch, aids_batch, lpfs_batch, sds_batch, formulas = (
                        self._sampler.sample_batch(self.model, cfg.batch_size)
                    )
                else:
                    tids_batch, aids_batch, lpfs_batch, sds_batch, formulas = (
                        self.model.sample_batch(cfg.batch_size, temperature=cfg.train_temperature)
                    )

                # Phase 2: 计算奖励
                rewards = []
                valid_formulas = []
                for formula in formulas:
                    if formula:
                        r = self.reward_computer.compute(formula)
                    else:
                        r = -1.0
                    rewards.append(r)
                    if r > 0:
                        valid_formulas.append((formula, r))
                all_rewards.extend(rewards)
                all_valid_formulas.extend(valid_formulas)

                # Phase 3: 准备 SubTB loss 输入
                # log_rewards must match batch_size — use epsilon for invalid trajectories
                log_reward_vals = []
                for r in rewards:
                    if r > 0:
                        log_reward_vals.append(math.log(r + cfg.reward_epsilon))
                    else:
                        log_reward_vals.append(math.log(cfg.reward_epsilon))

                log_rewards = torch.tensor(log_reward_vals, dtype=torch.float32)

                # Phase 4: SubTB loss (在梯度累积循环中累积)
                n_valid = len([r for r in rewards if r > 0])
                if n_valid > 0:
                    loss = self.model.compute_subtb_loss(
                        tids_batch, sds_batch, lpfs_batch,
                        log_rewards, lambda_=subtb_lambda,
                    )
                else:
                    # 无有效轨迹 → fallback to TB loss
                    log_pf_sums = [sum(lpfs) for lpfs in lpfs_batch]
                    num_trans = [len(lpfs) for lpfs in lpfs_batch]
                    loss = self.model.compute_tb_loss_vectorized(
                        log_pf_sums, num_trans, log_rewards
                    )

                # 按累积步数缩放损失
                loss = loss / accum_steps
                loss.backward()

            # ─── Phase 5: 梯度裁剪 + 优化 ───
            torch.nn.utils.clip_grad_norm_(
                self.model.parameters(), cfg.grad_clip
            )

            self.optimizer.step()
            self.optimizer_logZ.step()
            self.optimizer_flow.step()

            self.optimizer.zero_grad()
            self.optimizer_logZ.zero_grad()
            self.optimizer_flow.zero_grad()

            # Scheduler step
            if self.scheduler and (ep >= cfg.warmup_episodes or warmup_done):
                self.scheduler.step()
                self.scheduler_logZ.step()
                self.scheduler_flow.step()

            if ep >= cfg.warmup_episodes:
                warmup_done = True

            # ─── 日志 (使用累积后的 loss 近似) ───
            if ep == 0 or (ep + 1) % cfg.log_every == 0:
                self._log_metrics(
                    ep, all_rewards, all_valid_formulas, t_start, verbose=verbose
                )

            # ─── 检查点 ───
            if (ep + 1) % cfg.save_every == 0:
                self.save_checkpoint()

            # ─── 追踪最佳 ───
            n_valid = len([r for r in all_rewards if r > 0])
            if n_valid > 0:
                # loss from last micro-batch (already scaled by 1/accum_steps)
                loss_val = loss.item() * accum_steps  # undo scaling for logging
                if loss_val < self.best_loss:
                    self.best_loss = loss_val

        # 训练结束
        if verbose:
            elapsed = time.time() - t_start
            print(f"\n[Train] Done. {n_episodes} episodes in {elapsed:.1f}s "
                  f"({elapsed / n_episodes:.2f}s/ep)")
            pool_stats = self.reward_computer.get_pool_stats()
            print(f"[Pool]  {pool_stats['size']} factors "
                  f"({pool_stats.get('unique_structures', '?')} unique), "
                  f"mean|IC|={pool_stats['mean_ic']:.4f}, "
                  f"max|IC|={pool_stats['max_ic']:.4f}")
            print(f"[Model] logZ={self.model.logZ.item():.4f}, "
                  f"best_loss={self.best_loss:.4f}")

            # v4: dual-channel sampler stats
            if self._sampler:
                stats = self._sampler.get_stats()
                print(f"[Sampler] {stats['total']} trajectories: "
                      f"{stats['exploit_pct']}% exploit, {stats['explore_pct']}% explore")

            # v4: evaluator summary
            if self._use_v4:
                print()
                self._print_eval_summary()

    def _log_metrics(
        self,
        episode: int,
        rewards: List[float],
        valid_formulas: List[tuple],
        t_start: float,
        verbose: bool = True,
    ):
        """输出训练指标"""
        elapsed = time.time() - t_start

        # 奖励统计
        pos_rewards = [r for r in rewards if r > 0]
        neg_count = len(rewards) - len(pos_rewards)

        pool_stats = self.reward_computer.get_pool_stats()

        # 操作符多样性
        op_usage = self._count_operator_usage(valid_formulas)

        metrics = {
            "episode": episode + 1,
            "tb_loss": round(self.best_loss, 4),
            "logZ": round(self.model.logZ.item(), 4),
            "mean_reward": round(sum(rewards) / len(rewards), 4) if rewards else 0,
            "max_reward": round(max(rewards), 4) if rewards else 0,
            "valid_count": len(pos_rewards),
            "invalid_count": neg_count,
            "pool_size": pool_stats["size"],
            "pool_unique": pool_stats.get("unique_structures", pool_stats["size"]),
            "pool_max_ic": round(pool_stats["max_ic"], 4),
            "elapsed": round(elapsed, 1),
        }
        self.metrics_history.append(metrics)

        # v4: feed evaluator
        self.evaluator.record_episode(
            episode=episode + 1,
            valid_formulas=valid_formulas,
            total_sampled=len(rewards),
            archive=self.reward_computer.archive if self._use_v4 else None,
            pool_max_ic=pool_stats.get("max_ic", 0.0),
            pool_size=pool_stats["size"],
            factor_pool=self.reward_computer.factor_pool,
        )

        if verbose:
            mode_tag = "[v4-DC]" if self._sampler else ("[v4]" if self._use_v4 else "[v3]")
            print(
                f"{mode_tag} [Ep {episode + 1:4d}] "
                f"valid={len(pos_rewards)}/{len(rewards)} "
                f"mean_r={metrics['mean_reward']:.3f} "
                f"max_r={metrics['max_reward']:.3f} "
                f"pool={pool_stats['size']} "
                f"best_ic={pool_stats['max_ic']:.4f} "
                f"logZ={self.model.logZ.item():.4f} "
                f"({elapsed:.0f}s)"
            )

            if op_usage:
                top_ops = sorted(op_usage.items(), key=lambda x: -x[1])[:5]
                print(f"        top ops: {', '.join(f'{op}({cnt})' for op, cnt in top_ops)}")

            # v4: show archive category coverage
            if self._use_v4 and pool_stats.get('categories'):
                cats = pool_stats['categories']
                filled = sum(1 for c in cats.values() if c['fill_rate'] > 0)
                total = len(cats)
                print(f"        archive: {filled}/{total} cats filled, "
                      f"struct_div={pool_stats.get('unique_structures', '?')}")
            elif self._use_v4:
                print(f"        struct_div={pool_stats.get('unique_structures', '?')} "
                      f"(no archive)")

    def _count_operator_usage(self, valid_formulas: List[tuple]) -> dict:
        """统计操作符使用频率"""
        from .grammar import TOKEN_BY_NAME

        op_counts = {}
        for formula, _ in valid_formulas:
            for token_def in TOKEN_BY_NAME.values():
                if token_def.arity > 0 and token_def.name in formula:
                    op_counts[token_def.name] = op_counts.get(token_def.name, 0) + 1

        return op_counts

    def _print_eval_summary(self):
        """Print v4 multi-objective evaluator summary."""
        report = self.evaluator.summary()
        best = report.get("best_metrics", {})
        trends = report.get("trends", {})
        mode_collapse = report.get("mode_collapse_detected", False)

        print(f"[Eval] v4 Multi-Objective Summary:")
        print(f"  best_oos_ic:          {best.get('best_pool_max_ic', 0):.4f}")
        print(f"  best_effective_factors:{best.get('best_effective_factors', 0)}")
        print(f"  best_valid_rate:      {best.get('best_valid_rate', 0):.3f}")
        print(f"  best_unique_structures:{best.get('best_unique_structures', 0)}")
        print(f"  ic_stagnant:          {trends.get('ic_stagnant', False)}")
        print(f"  entropy_trend:        {trends.get('entropy_trend', '?')}")
        print(f"  mode_collapse:        {mode_collapse}")

        recs = report.get("recommendations", [])
        if recs:
            print(f"  recommendations:")
            for rec in recs:
                print(f"    - {rec}")

    def export_eval_summary(self, path: str = None) -> str:
        """Export v4 evaluator summary to JSON."""
        import json as _json
        if path is None:
            path = "eval_summary.json"
        report = self.evaluator.summary()
        report["history"] = self.evaluator.export_history()
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(report, f, indent=2, default=str)
        return path

    def save_checkpoint(self, path: str = None):
        """保存检查点 (v2.2: 含 flow_predictor 状态)"""
        if path is None:
            os.makedirs(self.config.checkpoint_dir, exist_ok=True)
            path = os.path.join(
                self.config.checkpoint_dir,
                f"gfn_ep{self.episode + 1:04d}.pt",
            )

        checkpoint = {
            "episode": self.episode + 1,
            "model_state": self.model.state_dict(),
            "optimizer_state": self.optimizer.state_dict(),
            "optimizer_logZ_state": self.optimizer_logZ.state_dict(),
            "optimizer_flow_state": self.optimizer_flow.state_dict(),
            "logZ": self.model.logZ.item(),
            "config": self.config.to_dict(),
            "pool_size": len(self.reward_computer.factor_pool) if self.reward_computer else 0,
            "factor_pool": list(self.reward_computer.factor_pool) if self.reward_computer else [],
            "metrics": self.metrics_history,
            "timestamp": datetime.now().isoformat(),
        }
        torch.save(checkpoint, path)

    def load_checkpoint(self, path: str):
        """加载检查点 (v2.2: 含 flow_predictor 恢复)"""
        checkpoint = torch.load(path, map_location=self.config.device, weights_only=False)

        self.model.load_state_dict(checkpoint["model_state"])
        self.optimizer.load_state_dict(checkpoint["optimizer_state"])
        self.optimizer_logZ.load_state_dict(checkpoint["optimizer_logZ_state"])

        # 恢复 flow optimizer（可能不存在于旧检查点）
        if "optimizer_flow_state" in checkpoint:
            self.optimizer_flow.load_state_dict(checkpoint["optimizer_flow_state"])

        self.episode = checkpoint.get("episode", 0)
        self.metrics_history = checkpoint.get("metrics", [])

        # Restore config if provided (but don't overwrite reward weights)
        saved_config = checkpoint.get("config", {})
        reward_keys = {"reward_ic_weight", "reward_diversity_weight", "reward_validity_weight"}
        for k, v in saved_config.items():
            if hasattr(self.config, k) and k not in reward_keys:
                setattr(self.config, k, v)

        # Store saved pool for restoration when reward_computer is created
        self._saved_factor_pool = checkpoint.get("factor_pool", [])

    def export_metrics(self, path: str = None) -> str:
        """导出训练指标为 JSON"""
        if path is None:
            os.makedirs(self.config.checkpoint_dir, exist_ok=True)
            path = os.path.join(self.config.checkpoint_dir, "training_metrics.json")

        with open(path, "w") as f:
            json.dump(self.metrics_history, f, indent=2)

        return path

    def get_top_factors(self, n: int = 10) -> List[tuple]:
        """获取奖励计算器池中的最佳因子"""
        if not self.reward_computer:
            return []

        sorted_pool = sorted(self.reward_computer.factor_pool, key=lambda x: -abs(x[1]))
        return sorted_pool[:n]
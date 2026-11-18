"""
GFlowNet 状态编码器 — Transformer 编码因子表达式 token 序列

将前缀 token 序列编码为固定维度的状态向量，供前向/后向策略网络使用。

编码流程:
  Token Sequence (len=L) + Stack Depth
      ↓
  Token Embedding (L × d_model) + Position Encoding
      ↓
  Transformer Encoder (N layers)
      ↓
  Mean Pooling → state_vec (d_model)
      ↓
  Concat with stack_depth_embed → final_state (d_model + stack_embed_dim)
"""

import math
import torch
import torch.nn as nn
import torch.nn.functional as F

from .config import GFNConfig
from .grammar import VOCAB_SIZE, PAD_IDX


class PositionalEncoding(nn.Module):
    """正弦位置编码"""

    def __init__(self, d_model: int, max_len: int = 50):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model)
        )
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0))  # (1, max_len, d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        参数:
            x: (batch, seq_len, d_model)
        返回:
            (batch, seq_len, d_model) with position encoding added
        """
        return x + self.pe[:, : x.size(1), :]


class FactorStateEncoder(nn.Module):
    """
    因子表达式状态编码器。

    输入:  token_ids (batch, seq_len), stack_depth (batch,)
    输出:  (batch, d_model + stack_embed_dim) — 用于策略网络的 pooled state
           (batch, seq_len, d_model) — per-token embeddings (backward policy 可能用到)
    """

    def __init__(self, config: GFNConfig):
        super().__init__()

        self.d_model = config.d_model
        self.stack_embed_dim = config.stack_depth_embed_dim
        self.max_len = config.max_expr_len + 2  # +BOS +EOS

        # Token embedding
        self.token_embedding = nn.Embedding(VOCAB_SIZE, config.d_model, padding_idx=PAD_IDX)

        # Position encoding
        self.pos_encoding = PositionalEncoding(config.d_model, self.max_len)

        # Transformer encoder
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.n_heads,
            dim_feedforward=config.d_ff,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=config.n_layers)

        # Stack depth embedding
        self.stack_embedding = nn.Embedding(self.max_len + 1, config.stack_depth_embed_dim)

        # Dropout
        self.dropout = nn.Dropout(config.dropout)

    def forward(
        self,
        token_ids: torch.Tensor,
        stack_depth: torch.Tensor,
    ) -> dict:
        """
        参数:
            token_ids:   (batch, seq_len) — token 索引 (包含 BOS)
            stack_depth: (batch,) — 当前栈深度

        返回:
            dict with:
              "pooled":   (batch, d_model + stack_embed_dim) — final state
              "per_token": (batch, seq_len, d_model) — per-position embeddings
        """
        batch_size, seq_len = token_ids.shape

        # Token embeddings
        x = self.token_embedding(token_ids)  # (batch, seq_len, d_model)

        # Position encoding
        x = self.pos_encoding(x)
        x = self.dropout(x)

        # Padding mask (True = ignore)
        pad_mask = (token_ids == PAD_IDX)  # (batch, seq_len)

        # Transformer encode
        x = self.transformer(x, src_key_padding_mask=pad_mask)  # (batch, seq_len, d_model)

        # Mean pool over non-padded positions
        # Create mask for valid positions
        valid_mask = (~pad_mask).float().unsqueeze(-1)  # (batch, seq_len, 1)
        pooled = (x * valid_mask).sum(dim=1) / valid_mask.sum(dim=1).clamp(min=1)
        # pooled: (batch, d_model)

        # Stack depth embedding
        stack_depth_clamped = torch.clamp(stack_depth, 0, self.max_len).long()
        stack_emb = self.stack_embedding(stack_depth_clamped)  # (batch, stack_embed_dim)

        # Concatenate
        final_state = torch.cat([pooled, stack_emb], dim=-1)  # (batch, d_model + stack_embed_dim)

        return {
            "pooled": final_state,
            "per_token": x,
        }

    def get_output_dim(self) -> int:
        """返回状态向量维度"""
        return self.d_model + self.stack_embed_dim
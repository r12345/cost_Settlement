"""AI 相关数据模型：支出向量（语义检索用）。

AI 依然不产生业务数据：这里的 embedding 只是已存在支出的派生索引，
由 ``ExpenseService`` 在事务内后置触发，生成失败不影响支出本身。
"""

from django.db import models
from pgvector.django import HnswIndex, VectorField

EMBEDDING_DIMENSIONS = 1536


class ExpenseEmbedding(models.Model):
    """一笔支出的向量表示，供账本语义检索与问答使用。"""

    expense = models.OneToOneField(
        "expenses.Expense",
        on_delete=models.CASCADE,
        related_name="embedding",
    )
    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="expense_embeddings",
    )
    content = models.TextField(help_text="生成 embedding 的原始文本，用于调试和重建")
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "支出向量"
        verbose_name_plural = "支出向量"
        indexes = [
            HnswIndex(
                name="expense_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return "embedding#{}".format(self.expense_id)

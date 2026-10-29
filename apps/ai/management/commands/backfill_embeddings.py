"""为历史支出补齐语义检索用的 embedding。

用法：

    python manage.py backfill_embeddings
    python manage.py backfill_embeddings --group-id 24
    python manage.py backfill_embeddings --dry-run

只处理未软删除的支出；已存在且 content 与当前内容一致的记录会跳过。
AI 未启用时直接失败退出，避免静默什么都没做。
"""

from django.core.management.base import BaseCommand, CommandError

from apps.ai.models import ExpenseEmbedding
from apps.ai.services import ExpenseEmbeddingService
from apps.expenses.models import Expense


class Command(BaseCommand):
    help = "为缺少或已过期的支出 embedding 做回填（只处理 is_deleted=False 的支出）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--group-id", type=int, default=None, help="只回填指定群组的支出"
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="只统计待处理的条数，不写入任何数据"
        )

    def handle(self, *args, **options):
        if not ExpenseEmbeddingService.is_enabled():
            raise CommandError("未配置 OPENAI_API_KEY，无法生成 embedding")

        queryset = (
            Expense.objects.filter(is_deleted=False)
            .select_related("payer")
            .prefetch_related("splits__user")
            .order_by("id")
        )

        group_id = options.get("group_id")
        if group_id is not None:
            queryset = queryset.filter(group_id=group_id)

        dry_run = options.get("dry_run", False)
        total = queryset.count()

        existing_content = dict(
            ExpenseEmbedding.objects.filter(expense__in=queryset).values_list(
                "expense_id", "content"
            )
        )

        success = failed = skipped = pending = 0

        for index, expense in enumerate(queryset.iterator(chunk_size=100), start=1):
            content = ExpenseEmbeddingService.build_content(expense)

            if existing_content.get(expense.id) == content:
                skipped += 1
            else:
                pending += 1
                if not dry_run:
                    ExpenseEmbeddingService.upsert(expense)
                    saved = ExpenseEmbedding.objects.filter(
                        expense_id=expense.id, content=content
                    ).exists()
                    if saved:
                        success += 1
                    else:
                        failed += 1

            if index % 10 == 0:
                self.stdout.write("已处理 {}/{}".format(index, total))

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "完成（dry-run，未写入）：共 {} 条，待生成 {}、跳过 {}".format(
                        total, pending, skipped
                    )
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "完成：共 {} 条，成功 {}、失败 {}、跳过 {}".format(
                        total, success, failed, skipped
                    )
                )
            )

from django.core.exceptions import ValidationError
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("Ngày tạo", auto_now_add=True)
    updated_at = models.DateTimeField("Ngày cập nhật", auto_now=True)

    class Meta:
        abstract = True


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Không được sửa bản ghi lịch sử.")

    def delete(self):
        raise ValidationError("Không được xóa bản ghi lịch sử.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Không được sửa bản ghi lịch sử.")

    def bulk_create(self, objs, **kwargs):
        if kwargs.get("update_conflicts"):
            raise ValidationError("Không được ghi đè bản ghi lịch sử.")
        return super().bulk_create(objs, **kwargs)


class ImmutableModel(models.Model):
    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, **kwargs):
        if not self._state.adding or kwargs.get("force_update") or kwargs.get("update_fields"):
            raise ValidationError("Không được sửa bản ghi lịch sử.")
        kwargs["force_insert"] = True
        return super().save(**kwargs)

    def delete(self, **kwargs):
        raise ValidationError("Không được xóa bản ghi lịch sử.")


class NoDeleteQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Hãy vô hiệu hóa phần thưởng thay vì xóa.")

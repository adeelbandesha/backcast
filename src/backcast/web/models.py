from datetime import timedelta

from django.db import models
from django.urls import reverse
from django.utils import timezone

# A run still queued or running after this long was lost, say to a worker restart, and may be started again.
STALLED_AFTER = timedelta(minutes=15)


class Run(models.Model):
    """One backtest. The id fingerprints its inputs, so the same prices, settings and code always share a run."""

    QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"

    id = models.CharField(primary_key=True, max_length=16)
    symbol = models.CharField(max_length=20)
    horizon = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=200)
    data_date = models.DateField()
    code_version = models.CharField(max_length=100)
    settings = models.JSONField()
    status = models.CharField(max_length=10, default=QUEUED)
    error = models.TextField(blank=True)
    # The rendered results, so a permalink keeps showing exactly what it showed when the run finished.
    html = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def get_absolute_url(self):
        return reverse("run", args=[self.id])

    @property
    def target(self) -> str:
        """What was forecast: "price" or "volatility". Runs from before the volatility track have no target."""
        return self.settings.get("target", "price")

    @property
    def latest_url(self) -> str:
        """The same backtest on the latest prices, which may be a newer run."""
        return f"{reverse('backtest' if self.target == 'price' else 'volatility', args=[self.symbol])}?h={self.horizon}"

    @property
    def stalled(self) -> bool:
        return self.status in (self.QUEUED, self.RUNNING) and timezone.now() - self.updated > STALLED_AFTER

    @property
    def failed(self) -> bool:
        return self.status == self.FAILED or self.stalled

    @property
    def message(self) -> str:
        return self.error or "This run stopped before it finished."

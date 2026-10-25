from ..data import attribution
from ..evaluate import ONE_WAY
from ..models import MODELS


def site(request):
    # The models the app runs today. A saved run and the study each record the models they actually used.
    return {"price_attribution": attribution(), "rivals": len(MODELS) - 1, "one_way": f"{ONE_WAY:.0%}"}

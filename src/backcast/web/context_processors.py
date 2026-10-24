from ..data import attribution


def price_source(request):
    return {"price_attribution": attribution()}

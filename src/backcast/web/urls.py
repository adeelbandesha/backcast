from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("backtest/<str:symbol>/", views.backtest, name="backtest"),
    path("backtest/<str:symbol>/results/", views.backtest_results, name="backtest-results"),
    path("models/", views.models, name="models"),
    path("method/", views.method, name="method"),
    path("api/search/", views.search, name="search"),
]

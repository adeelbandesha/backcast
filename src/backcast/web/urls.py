from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("backtest/<str:symbol>/", views.backtest, name="backtest"),
    path("runs/<slug:run_id>/", views.run, name="run"),
    path("runs/<slug:run_id>/results/", views.run_results, name="run-results"),
    path("models/", views.models, name="models"),
    path("method/", views.method, name="method"),
    path("api/search/", views.search, name="search"),
]

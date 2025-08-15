from django.urls import path
from .views import TotalView, IndexView, ProgressView, CaptureView

app_name = "donations"

urlpatterns = [
    path("", IndexView.as_view(), name="index"),
    path("api/total/", TotalView.as_view(), name="total"),
    path("partials/progress/", ProgressView.as_view(), name="progress"),
    path("api/capture/", CaptureView.as_view(), name="capture"),
]

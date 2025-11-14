from django.urls import re_path
from .views import upload_file, execute

urlpatterns = [
    re_path(r'^upload/?$', upload_file),
    re_path(r'^execute/?$', execute),
]

from django.db import models

class UploadedFile(models.Model):
    file = models.FileField(upload_to='uploads/')
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_name or self.file.name

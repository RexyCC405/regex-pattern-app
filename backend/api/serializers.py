from rest_framework import serializers

class UploadResponseSerializer(serializers.Serializer):
    file_id = serializers.IntegerField()
    filename = serializers.CharField()
    is_excel = serializers.BooleanField()
    columns = serializers.ListField(child=serializers.CharField())
    head = serializers.ListField()

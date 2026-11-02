from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [("catalog", "0008_productimage_medium")]
    operations = [TrigramExtension()]

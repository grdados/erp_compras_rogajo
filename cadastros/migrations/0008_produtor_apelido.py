from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0007_alter_fornecedor_cep_alter_fornecedor_cidade_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="produtor",
            name="apelido",
            field=models.CharField(blank=True, max_length=80),
        ),
    ]


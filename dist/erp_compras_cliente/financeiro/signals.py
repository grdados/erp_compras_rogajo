from django.db.models.signals import post_save
from django.dispatch import receiver

from compras.models import PedidoCompra

from .models import Faturamento
from .services import processar_faturamento, processar_pedido_compra


@receiver(post_save, sender=PedidoCompra)
def pedido_compra_pos_save(sender, instance, created, **kwargs):
    processar_pedido_compra(instance, created=created)


@receiver(post_save, sender=Faturamento)
def faturamento_pos_save(sender, instance, created, **kwargs):
    processar_faturamento(instance)

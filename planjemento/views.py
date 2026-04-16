from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def painel_planejamento(request):
    contexto = {
        "titulo": "Planejamentos",
        "total_planejamentos": 0,
        "planejamentos_ativos": 0,
        "planejamentos_concluidos": 0,
    }
    return render(request, "planejamento/painel_planejamento.html", contexto)

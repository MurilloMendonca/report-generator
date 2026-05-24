# Layout Library for AI PDF Report Generator

BLUEPRINTS = {
    "executive_standard": {
        "name": "Padrão Executivo",
        "description": "Resumo no topo, 2 colunas de gráficos no meio, conclusão no rodapé.",
        "slots": [
            {"id": "kpi_resumo", "x": 10, "y": 30, "w": 190, "h": 40},
            {"type": "graphic", "x": 10, "y": 75, "w": 90, "h": 60},
            {"type": "graphic", "x": 110, "y": 75, "w": 90, "h": 60},
            {"type": "graphic", "x": 10, "y": 140, "w": 90, "h": 60},
            {"type": "graphic", "x": 110, "y": 140, "w": 90, "h": 60},
            {"id": "kpi_conclusao", "x": 10, "y": 235, "w": 190, "h": 50}
        ]
    },
    "data_heavy": {
        "name": "Dashboard Denso",
        "description": "Focado em múltiplas visualizações lado a lado.",
        "slots": [
            {"id": "kpi_resumo", "x": 10, "y": 30, "w": 190, "h": 30},
            {"type": "graphic", "x": 10, "y": 65, "w": 60, "h": 50},
            {"type": "graphic", "x": 75, "y": 65, "w": 60, "h": 50},
            {"type": "graphic", "x": 140, "y": 65, "w": 60, "h": 50},
            {"type": "graphic", "x": 10, "y": 120, "w": 190, "h": 70},
            {"type": "graphic", "x": 10, "y": 195, "w": 90, "h": 50},
            {"type": "graphic", "x": 110, "y": 195, "w": 90, "h": 50},
            {"id": "kpi_conclusao", "x": 10, "y": 250, "w": 190, "h": 35}
        ]
    }
}

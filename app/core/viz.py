import plotly.graph_objects as go
import plotly.io as pio


def generar_grafico_ajuste(nombre_programa, original, vigente, real):
    fig = go.Figure(data=[
        go.Bar(name='Nominal Original (2023)', x=[nombre_programa], y=[original]),
        go.Bar(name='Nominal Vigente (Actual)', x=[nombre_programa], y=[vigente]),
        go.Bar(name='Poder de Compra Real', x=[nombre_programa], y=[real])
    ])

    fig.update_layout(
        title=f"Impacto del Ajuste: {nombre_programa}",
        yaxis_title="Pesos ($)",
        template="gridon",
        barmode='group'
    )

    # Retorna el gráfico como HTML para embeber en FastAPI
    return pio.to_html(fig, full_html=False)
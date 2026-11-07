# TrendRadar 6.11.0, upstream source revision ceabb027fe9bbdafec59dbbfa12cb46c2ba256ce.
FROM wantcat/trendradar@sha256:3effcb5cedf5c385b50a6a32cd2599cbdbf54c9d8c52c0403406e356f88df80b
LABEL org.opencontainers.image.title="TrendRadar Local" \
      org.opencontainers.image.licenses="GPL-3.0-only" \
      org.opencontainers.image.description="Local news monitoring and configurable focus"
WORKDIR /app
RUN mkdir -p /app/custom_layout /app/ai_budget /app/focus_private \
    && cp /app/trendradar/report/html.py /app/custom_layout/upstream_html.py \
    && cp /app/trendradar/ai/analyzer.py /app/ai_budget/upstream_analyzer.py
COPY custom_layout/ /app/custom_layout/
COPY ai_budget/ /app/ai_budget/
COPY config/ /app/config/
COPY scripts/bootstrap.py scripts/initialize_budget.py /app/local_tools/
RUN cp /app/custom_layout/manage_focus.py /app/manage.py \
    && cp /app/custom_layout/renderer.py /app/trendradar/report/html.py \
    && cp /app/ai_budget/client.py /app/trendradar/ai/client.py \
    && cp /app/ai_budget/analyzer.py /app/trendradar/ai/analyzer.py
ENTRYPOINT ["python", "/app/local_tools/bootstrap.py"]

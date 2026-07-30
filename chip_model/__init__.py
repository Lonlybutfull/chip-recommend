"""AISHPerf Knowledge Graph — AI Chip × Model knowledge graph package.

Core modules:
    database — SQLite DB layer (connection, filters, queries, writes)
    server   — FastAPI REST API backend
    cli_app  — Typer CLI (12 commands, 5 groups)
    config   — YAML-based configuration management

Pipeline subpackage:
    pipeline.seed              — Master seeding script
    pipeline.crawl             — Full-scale web crawling orchestrator
    pipeline.enrich            — Automated data enrichment pipeline
    pipeline.extract_chips     — Chip spec extraction from crawled pages
    pipeline.extract_benchmarks — Benchmark data extraction
    pipeline.extract_prices    — Price data extraction

Legacy:
    legacy — Historical batch enrichment script
"""

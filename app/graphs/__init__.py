"""The LangGraph workflows. Each file builds one graph:

    chat.py     the router that picks a workflow, plus the compiled patient chat graph
    triage.py   W1 find my doctor      (chain + rule gate + clarifying loop + parallel tools + pause)
    records.py  W2 ask my records      (chain + two bounded evaluator loops)
    ingest.py   W2 upload a report     (background chain)
    scribe.py   W3 consultation scribe (chain + checks + a pause for the doctor's signature)
"""

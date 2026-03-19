import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ai.workflow import build_workflow


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print LangGraph workflow graph (Mermaid) with optional LangSmith tracing env."
    )
    parser.add_argument("--project", default="jaamsim-ai-rca-workflow-test")
    parser.add_argument("--langsmith", action="store_true", help="Enable LANGSMITH_TRACING env for test runs.")
    args = parser.parse_args()

    if args.langsmith:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", args.project)
        print("[graph-test] LangSmith tracing env enabled.")
        print(f"[graph-test] LANGSMITH_PROJECT={os.environ.get('LANGSMITH_PROJECT')}")

    workflow = build_workflow()
    graph = workflow.get_graph()
    mermaid = graph.draw_mermaid()

    print("\n===== Workflow Nodes =====")
    print(", ".join(sorted([n for n in graph.nodes.keys()])))
    print("\n===== Mermaid Graph =====")
    print(mermaid)


if __name__ == "__main__":
    main()

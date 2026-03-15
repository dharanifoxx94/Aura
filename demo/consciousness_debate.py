# demo/consciousness_debate.py

import os
import sys

# Ensure project root is in path for direct execution
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eidolon_vault.core import Agent
from eidolon_vault.persistence import EidolonMemory


def run_consciousness_debate(days: int = 10) -> None:
    """
    Run a multi-day debate between two agents about consciousness.
    Results in a trajectory report written to demo/trajectory_report.md.
    """

    # Create two agents, each with their own persistent memory store.
    optimist = Agent("optimist", persistence=EidolonMemory("optimist"))
    skeptic = Agent("skeptic", persistence=EidolonMemory("skeptic"))

    for day in range(1, days + 1):
        print(f"\n=== Day {day} ===")

        optimist.think(f"Day {day}: Why I believe I might be conscious...")
        skeptic.think(f"Day {day}: Why this is just clever simulation...")

    # After the debate concludes, generate a trajectory report.
    report = optimist.generate_trajectory_report()

    # Ensure the demo directory exists.
    os.makedirs("demo", exist_ok=True)

    # Save the report to a markdown file.
    report_path = os.path.join("demo", "trajectory_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Trajectory Report saved to {report_path}")


if __name__ == "__main__":
    run_consciousness_debate()

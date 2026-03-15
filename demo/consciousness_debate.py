# demo/consciousness_debate.py
import sys
import os
# Add the project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from eidolon_vault.core import Agent
from eidolon_vault.persistence import EidolonMemory

def run_consciousness_debate(days: int = 10):
    optimist = Agent("optimist", persistence=EidolonMemory("optimist"))
    skeptic = Agent("skeptic", persistence=EidolonMemory("skeptic"))

    for day in range(1, days+1):
        print(f"\n=== Day {day} ===")
        optimist.think(f"Day {day}: Why I believe I might be conscious...")
        skeptic.think(f"Day {day}: Why this is just clever simulation...")

    report = optimist.generate_trajectory_report()
    os.makedirs("demo", exist_ok=True)
    with open("demo/trajectory_report.md", "w") as f:
        f.write(report)
    print("✅ Trajectory Report saved to demo/trajectory_report.md")

if __name__ == "__main__":
    run_consciousness_debate()

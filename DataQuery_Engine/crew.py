from agents.ground_truth_agent import ground_truth_agent
from agents.search_agent import search_agent
from agents.scoring_agent import scoring_agent
from tasks.ground_truth_task import ground_truth_task
from tasks.search_task import search_task
from tasks.scoring_task import scoring_task

def run_full_pipeline():
    crew = Crew(
        agents=[ground_truth_agent, search_agent, scoring_agent],
        tasks=[ground_truth_task, search_task, scoring_task],
        verbose=True
    )
    results = crew.kickoff()
    print("Pipeline complete. Final Results:", results)

if __name__ == "__main__":
    run_full_pipeline()
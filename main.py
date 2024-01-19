# stdlib
import asyncio
import logging
import os
import sys
from typing import Dict, List

# third party
import httpx
import pandas as pd

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# dbt Cloud variables
ACCOUNT_ID = os.getenv("DBT_CLOUD_ACCOUNT_ID", None)
TOKEN = os.getenv("DBT_CLOUD_SERVICE_TOKEN", None)
HOST = os.getenv("DBT_CLOUD_HOST", "cloud.getdbt.com")
JOB_ID = os.getenv("DBT_CLOUD_JOB_ID", None)

# From GitHub Action
PULL_REQUEST_ID = int(os.getenv("PULL_REQUEST_ID", None))
REPO = os.getenv("GITHUB_REPOSITORY", None)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", None)
GIT_SHA = os.getenv("GIT_SHA", None)

# Derived variables
SCHEMA_OVERRIDE = f"dbt_cloud_pr_{JOB_ID}_{PULL_REQUEST_ID}"

# Run Status Indicators
SUCCESS = ":white_check_mark:"
FAILURE = ":x:"
CANCELLED = ":stop_sign:"

JOB_QUERY = """
query Job($jobId: BigInt!, $runId: BigInt, $schema: String) {
  job(id: $jobId, runId: $runId) {
    models(schema: $schema) {
      name
      uniqueId
      database
      access
      executionTime
    }
  }
}
"""

PUBLIC_MODELS_QUERY = """
query Account($accountId: BigInt!, $filter: PublicModelsFilter) {
  account(id: $accountId) {
    publicModels(filter: $filter) {
      uniqueId
      name
      dependentProjects {
        projectId
        defaultEnvironmentId
        dependentModelsCount
      }
    }
  }
}
"""

ENVIRONMENT_QUERY = """
query Lineage($environmentId: BigInt!, $filter: AppliedResourcesFilter!) {
  environment(id: $environmentId) {
    applied {
      lineage(filter: $filter) {
        uniqueId
        name
        publicParentIds
      }
    }
  }
}
"""


def full_url(path, metadata=False):
    include_metadata = "metadata." if metadata else ""
    return f"https://{include_metadata}{HOST}{path}"


def is_run_complete(run: Dict) -> bool:
    return run["status"] in [10, 20, 30]


def is_successful_run(run: Dict) -> bool:
    return run["status"] == 10


def remove_job(all_jobs: List[Dict], job_id_to_remove: int) -> List[Dict]:
    return [job for job in all_jobs if job["job_id"] != job_id_to_remove]


def get_run_status_emoji(status: int) -> str:
    status_dict = {10: SUCCESS, 20: FAILURE, 30: CANCELLED}
    return status_dict[status]


def run_status_formatted(run: Dict, duration: float) -> str:
    """Format a string indicating status of job.
    Args:
        run (dict): Dictionary representation of a Run
        time (float): Elapsed time since job triggered
    """
    status = run["status_humanized"]
    url = run["href"]
    return (
        f'\nStatus: "{status.capitalize()}"\nElapsed time: {duration}s\n'
        f"View here: {url}"
    )


async def dbt_cloud_api_request(path, *, method="get", metadata=False, **kwargs):
    url = full_url(path, metadata)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx.AsyncClient(headers=headers) as client:
        response = await getattr(client, method)(url, **kwargs)
        response.raise_for_status()
        return response.json()


async def trigger_job(account_id, job_id, payload) -> Dict:
    logger.info(f"Triggering CI job {job_id}")

    path = f"/api/v2/accounts/{account_id}/jobs/{job_id}/run/"
    response = await dbt_cloud_api_request(path, method="post", json=payload)

    try:
        run_id = response["data"]["id"]
    except KeyError:
        logger.error(f"Could not trigger job {job_id}")
        raise Exception(response["status"]["message"])

    while True:
        await asyncio.sleep(10)
        run_path = f"/api/v2/accounts/{account_id}/runs/{run_id}/"
        response = await dbt_cloud_api_request(run_path)
        run = response["data"]
        logger.info(run_status_formatted(run, run["duration"]))
        if is_run_complete(run):
            break

    return run


async def get_public_models_in_run(job_id: int, run_id: int, schema: str):
    path = "/beta/graphql"
    variables = {"jobId": job_id, "runId": run_id, "schema": schema}
    payload = {"query": JOB_QUERY, "variables": variables}
    results = await dbt_cloud_api_request(
        path, method="post", metadata=True, json=payload
    )
    models = results.get("data", {}).get("job", {}).get("models", [])
    logger.info(f"Models: {models}")
    return [
        model
        for model in models
        if model["access"].strip() == "public" and model["executionTime"] is not None
    ]


async def get_dependent_downstream_projects(public_models: List[Dict]):
    unique_ids = [model["uniqueId"] for model in public_models]
    variables = {"accountId": ACCOUNT_ID, "filter": {"uniqueIds": unique_ids}}
    payload = {"query": PUBLIC_MODELS_QUERY, "variables": variables}
    path = "/beta/graphql"
    results = await dbt_cloud_api_request(
        path, method="post", metadata=True, json=payload
    )
    models = results.get("data", {}).get("account", {}).get("publicModels", [])
    projects = dict()
    for model in models:
        for dep_project in model["dependentProjects"]:
            if dep_project["dependentModelsCount"] > 0:
                project_id = dep_project["projectId"]
                logger.info(
                    f"Downstream model found from {model['name']} in project {project_id}"
                )
                if project_id not in projects:
                    projects[project_id] = {
                        "environment_id": dep_project["defaultEnvironmentId"],
                        "models": [],
                    }
                projects[project_id]["models"].append(model["uniqueId"])
    return projects


async def get_ci_job(project_id: int):
    path = f"/api/v2/accounts/{ACCOUNT_ID}/jobs/"
    params = {"project_id": project_id}
    jobs = await dbt_cloud_api_request(path, params=params)
    ci_jobs = [job for job in jobs.get("data", []) if job["job_type"] == "ci"]
    try:
        return ci_jobs[0]
    except IndexError:
        return None


async def get_downstream_nodes(project_dict: Dict):
    variables = {
        "environmentId": project_dict["environment_id"],
        "filter": {"types": ["Model", "Snapshot"]},
    }
    payload = {"query": ENVIRONMENT_QUERY, "variables": variables}
    path = "/beta/graphql"
    results = await dbt_cloud_api_request(
        path, method="post", metadata=True, json=payload
    )
    lineage = (
        results.get("data", {})
        .get("environment", {})
        .get("applied", {})
        .get("lineage", [])
    )
    return [
        node
        for node in lineage
        if any(model in node["publicParentIds"] for model in project_dict["models"])
    ]


async def main():
    all_runs = []
    payload = {
        "cause": "Triggering CI Job from GH Action",
        "git_sha": GIT_SHA,
        "schema_override": SCHEMA_OVERRIDE,
        "github_pull_request_id": PULL_REQUEST_ID,
    }
    all_jobs = [{"job_id": JOB_ID, "payload": payload}]
    while all_jobs:
        # Trigger the CI jobs
        job_tasks = [
            trigger_job(ACCOUNT_ID, job["job_id"], job["payload"]) for job in all_jobs
        ]
        all_jobs.clear()

        for future in asyncio.as_completed(job_tasks):
            run = await future

            # Add run to list of all runs
            all_runs.append(run)

            if not is_successful_run(run):
                logger.info(f"Job {run['job_id']} was not successful.")
                continue

            # Any public models updated in the run?
            logger.info(f"Finding if any public models were updated in run {run['id']}")
            public_models = await get_public_models_in_run(
                run["job_id"], run["id"], SCHEMA_OVERRIDE
            )
            if not public_models:
                logger.info(f"No public models were updated in run {run['id']}.")
                continue

            logger.info(
                f"Finding any downstream projects with public models updated in run {run['id']}"
            )
            # Find downstream projects with possible downstream dependencies
            projects = await get_dependent_downstream_projects(public_models)

            if not projects:
                logger.info(
                    "No downstream projects found with the public models that were"
                    "updated."
                )
                continue

            # Loop through each project with
            for project_id, project_dict in projects.items():
                logger.info(f"Checking for downstream nodes in project {project_id}")
                nodes = await get_downstream_nodes(project_dict)
                if nodes:
                    logger.info(f"Found downstream nodes in project {project_id}")
                    steps_override = [
                        f'dbt build -s {"+ ".join([node["name"] for node in nodes]) + "+"} --vars \'{{ref_schema_override: {SCHEMA_OVERRIDE}}}\''
                    ]
                    job = await get_ci_job(project_id)
                    if job is not None:
                        logger.info(
                            f"CI job found in project {project_id} and will trigger shortly."
                        )
                        job_payload = {
                            "cause": "Triggering downstream CI job",
                            "steps_override": steps_override,
                            "schema_override": SCHEMA_OVERRIDE,
                        }
                        all_jobs.append({"job_id": job["id"], "payload": job_payload})

    # Only write back to the PR if there were multiple runs
    if len(all_runs) > 1:
        df = pd.DataFrame(all_runs[1:])
        df["status_emoji"] = df["status"].apply(get_run_status_emoji)
        df["url"] = df.apply(lambda x: f"[Run Details]({x['href']})", axis=1)
        df["is_downstream"] = df["job_id"].apply(lambda x: x == JOB_ID)
        df = df[["status_emoji", "project_id", "job_id", "duration_humanized", "url"]]
        df.columns = ["Status", "Project ID", "Job ID", "Duration", "URL"]
        markdown_df = df.to_markdown(index=False)
        comments = f"## Downstream CI Jobs\n\n{markdown_df}"
        payload = {"body": comments}
    else:
        payload = {"body": "## Downstream CI Jobs\n\nNo downstream dependencies found."}

    with httpx.Client(headers={"Authorization": f"Bearer {GITHUB_TOKEN}"}) as client:
        url = f"https://api.github.com/repos/{REPO}/issues/{PULL_REQUEST_ID}/comments"
        response = client.post(url, json=payload)
        response.raise_for_status()

    if any(not is_successful_run(run) for run in all_runs):
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())

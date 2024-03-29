# Downstream CI

This is a Github action that is designed specifically for dbt Cloud customers who have implemented dbt Mesh.  The overarching idea here is that customers want to understand how downstream projects will be impacted by changes in public models further upstream.

## Demo

https://www.loom.com/share/d6e99e8ae881403a84f5aaf25db68e77?sid=7e383c51-7216-4e41-8f7f-b5f11221f364

## Process

The diagram below shows how this action is designed:

![Downstream CI Process](assets/downstream_ci_process.png)

Additionally, the whole process is designed to be asynchronous so that downstream jobs will be triggered simultaneously when possible - but only triggered when their upstream counterpart completed successfully.

## Inputs

| **Name**                  | **Description**                                                                                                 | Required | Default            |
|---------------------------|-----------------------------------------------------------------------------------------------------------------|----------|--------------------|
| `dbt_cloud_service_token` | The service token generated from dbt Cloud.  **Ensure you have the proper permissions to trigger jobs**         | `True`   |                    |
| `dbt_cloud_account_id`    | This is the account ID which contains the projects you'll be triggering CI jobs for.                            | `True`   |                    |
| `dbt_cloud_job_id`        | This is the job ID corresponding to the CI job linked to the project you configure this action in.              | `True`   |                    |
| `github_token`             | GitHub token used to write back to the PR the results of the action.  You can use `${{ secrets.GITHUB_TOKEN }}`, which is available for all github action runs (so no additional setup, maintenanance of secrets on your part).                                                 | `False`  |             |
| `dbt_cloud_host`          | Where your dbt Cloud is located.                                                                                | `False`  | `cloud.getdbt.com` |
| `include_downstream`      | Whether to include downstream models (e.g. adding the `+` to the right of any models run in downstream CI jobs) | `False`  | `true`             |
| `dbt_command`             | The command to run within downstream CI jobs (`build` or `run`)                                                 | `False`  | `build`            |

## Caveats

### Slim CI

This action assumes that you have set up a CI job within each of your projects in your account.  If a CI job isn't found, then nothing will be triggered for that downstream project.  Additionally, when you configure your Slim CI job, continue to follow the instructions [here](https://docs.getdbt.com/docs/deploy/ci-jobs#set-up-ci-jobs) but ensure that your CI job is **NOT** triggered by pull requests (switch the toggle off)

![CI Job](assets/ci_job.png)

### `ref`

This whole process assumes that you have overridden the `ref` macro within each of the projects that you'd like for this process to work.  The macro below is what I have in each of my projects:

```sql
{% macro ref() %}

{% set rel = builtins.ref(*varargs, **kwargs) %}

{% set database_override = var('ref_database_override', none) %}
{% set schema_override = var('ref_schema_override', none) %}

{% if database_override %}
    {% set rel = rel.replace_path(database=database_override) %}
{% endif %}

{% if schema_override %}
    {% set rel = rel.replace_path(schema=schema_override) %}
{% endif %}

{% do return(rel) %}

{% endmacro %}
```

The important pieces are the `ref_schema_override` and `ref_database_override`.  When triggering the downstream CI jobs, we'll override the steps configured in the CI job to be:

1. Whatever models were found to be dependent on the upstream projects
2. The variable overrides for where we just built the upstream models

For example:

```
dbt build -s model_1+ model_2+ --vars '{ref_schema_override: dbt_cloud_pr_123456_5}'
```

Again, this just ensures that when we resolve the references in `model_1` and `model_2` (in the example above) that they're resolved to the schema and database where we just made changes to the upstream models they rely on.

## Example

```yaml
name: Downstream CI
on:
  pull_request:
    branches:
      - main
    types:
      - opened
      - reopened
      - synchronize
      - ready_for_review
jobs:
  trigger-dbt-job:
    runs-on: ubuntu-latest
    steps:
    - name: Checkout
      uses: actions/checkout@v2
    - name: dbt Cloud Downstream CI Action
      uses: dpguthrie/dbt-cloud-downstream-ci-action@0.6.3
      with:
        dbt_cloud_account_id: ${{ secrets.DBT_CLOUD_ACCOUNT_ID }}
        dbt_cloud_job_id: ${{ secrets.DBT_CLOUD_JOB_ID }}
        dbt_cloud_service_token: ${{ secrets.DBT_CLOUD_SERVICE_TOKEN }}
        github_token: ${{ secrets.GITHUB_TOKEN }}
```

## License

This project is licensed under the terms of the MIT license.

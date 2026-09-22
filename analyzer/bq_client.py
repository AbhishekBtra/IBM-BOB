"""BigQuery client — fetches stored procedure DDL from INFORMATION_SCHEMA."""

from google.cloud import bigquery


class BigQueryClient:
    """Thin wrapper around the BigQuery client for DDL retrieval."""

    def __init__(self, project_id: str, dataset_id: str, location: str = "US") -> None:
        """
        Initialise the BigQuery client.

        Args:
            project_id: GCP project that owns the dataset.
            dataset_id: BigQuery dataset containing the stored procedures.
            location:   BigQuery processing location (default: "US").
        """
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.location = location
        self._client = bigquery.Client(project=project_id)

    def get_sproc_ddl(self, sproc_name: str) -> str:
        """
        Fetch the DDL body of a stored procedure from INFORMATION_SCHEMA.ROUTINES.

        Args:
            sproc_name: The name of the stored procedure (ROUTINE_NAME).

        Returns:
            The ROUTINE_DEFINITION string (the full SQL body of the sproc).

        Raises:
            ValueError: If the sproc is not found in the dataset.
        """
        query = f"""
            SELECT ROUTINE_DEFINITION
            FROM `{self.project_id}.{self.dataset_id}.INFORMATION_SCHEMA.ROUTINES`
            WHERE ROUTINE_NAME = @sproc_name
              AND ROUTINE_TYPE = 'PROCEDURE'
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("sproc_name", "STRING", sproc_name)
            ],
            location=self.location,
        )

        results = self._client.query(query, job_config=job_config).result()
        rows = list(results)

        if not rows:
            raise ValueError(
                f"Stored procedure '{sproc_name}' not found in dataset "
                f"'{self.project_id}.{self.dataset_id}'. "
                "Check the sproc name in config.yaml and ensure the BigQuery "
                "credentials have the bigquery.routines.get permission."
            )

        return rows[0]["ROUTINE_DEFINITION"] or ""

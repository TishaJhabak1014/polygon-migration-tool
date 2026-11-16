from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """What the migration flow needs from any storage provider."""

    @staticmethod
    def test_case_keys(problem_id, test_number):
        # Same layout as the original Azure code (zero-padded test number).
        base = f"test_cases/{problem_id}/{test_number:02d}"
        return base, f"{base}.a"

    @abstractmethod
    def upload_bytes(self, container_name, key, data: bytes):
        """Upload raw bytes (overwrite if the key exists). Raise on failure."""

    @abstractmethod
    def empty_blob(self, container_name, problem_id):
        """Delete every object under test_cases/{problem_id}/."""

    def upload_test_case(self, container_name, db_problem_id, test_number, input_data, output_data):
        in_key, out_key = self.test_case_keys(db_problem_id, test_number)
        self.upload_bytes(container_name, in_key, input_data.encode("utf-8"))
        self.upload_bytes(container_name, out_key, output_data.encode("utf-8"))
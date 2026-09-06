from a13n_service.process.build import worker_build_id


def test_worker_build_identity_is_content_derived_and_not_a_runtime_setting(monkeypatch):
    first = worker_build_id()
    monkeypatch.setenv("FOUNDATION_BUILD_VERSION", "different-deployment-label")
    monkeypatch.setenv("FOUNDATION_SERVICE_INSTANCE_ID", "different-replica")
    assert worker_build_id() == first
    assert first.startswith("build-")
    assert len(first) == 70

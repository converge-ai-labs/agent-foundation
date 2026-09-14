"""Shared HTTP assertions for the seven-resource labels contract."""


async def assert_labels_http_contract(client, resource_path, collection_path, *, immutable_fields):
    before = (await client.get(resource_path)).json()
    path = resource_path + "/labels"
    initial = await client.get(path)
    assert initial.status_code == 200, initial.text
    labels = {"project": "alpha", "empty": "", "expression": "a=b"}
    updated = await client.put(path, headers={"If-Match": initial.headers["etag"]}, json={"labels": labels})
    assert updated.status_code == 200, updated.text
    after = (await client.get(resource_path)).json()
    assert after["labels"] == labels
    for field in immutable_fields:
        assert after[field] == before[field]
    noop = await client.put(path, headers={"If-Match": updated.headers["etag"]}, json={"labels": labels})
    assert noop.status_code == 200
    assert (await client.get(resource_path)).json()["updated_at"] == after["updated_at"]
    stale = await client.put(path, headers={"If-Match": initial.headers["etag"]}, json={"labels": {}})
    assert stale.status_code == 412, stale.text
    selected = await client.get(collection_path, params=[("label", "project=alpha"), ("label", "expression=a=b")])
    assert selected.status_code == 200, selected.text
    assert before["id"] in [row["id"] for row in selected.json()["items"]]
    absent = await client.get(collection_path, params={"label": "project=absent"})
    assert absent.status_code == 200 and absent.json()["items"] == []
    malformed = await client.put(path, headers={"If-Match": updated.headers["etag"]}, json={"labels": {"project": 42}})
    assert malformed.status_code == 400
    cleared = await client.put(path, headers={"If-Match": updated.headers["etag"]}, json={"labels": {}})
    assert cleared.status_code == 200 and cleared.json() == {"labels": {}}

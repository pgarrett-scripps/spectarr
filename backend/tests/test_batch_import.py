from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from spectarr.database import SessionLocal
from spectarr.models import Artifact, Job, Run


@pytest.mark.anyio
async def test_import_retry_reuses_run_and_source_after_processing(client, hierarchy):
    headers = {'Idempotency-Key': str(uuid4())}
    payload = {
        'experiment_id': hierarchy['experiment_id'], 'sample_id': hierarchy['sample_id'],
        'name': 'batch-one', 'source_class': 'open',
    }
    created = await client.post('/api/v1/runs', json=payload, headers=headers)
    assert created.status_code == 201
    run_id = created.json()['id']
    url = f'/api/v1/runs/{run_id}/artifacts/upload'
    uploaded = await client.post(url, files={'file': ('one.mgf', b'BEGIN IONS\n100 10\nEND IONS')}, headers=headers)
    assert uploaded.status_code == 201
    artifact_id = uploaded.json()['id']
    with SessionLocal() as session:
        # A worker may enrich metadata before a client retries a lost response.
        session.get(Run, run_id).metadata_json = {'spectra_count': 1}
        session.get(Artifact, artifact_id).metadata_json = {'latest_extraction': {'spectrum_count': 1}}
        session.commit()
        job_count = session.scalar(select(func.count(Job.id)))
    repeated_run = await client.post('/api/v1/runs', json=payload, headers=headers)
    assert repeated_run.status_code == 201
    assert repeated_run.json()['id'] == run_id
    repeated_source = await client.post(url, files={'file': ('one.mgf', b'BEGIN IONS\n100 10\nEND IONS')}, headers=headers)
    assert repeated_source.status_code == 201
    assert repeated_source.json()['id'] == artifact_id
    with SessionLocal() as session:
        assert session.scalar(select(func.count(Run.id)).where(Run.name == 'batch-one')) == 1
        assert session.scalar(select(func.count(Artifact.id)).where(Artifact.run_id == run_id)) == 1
        assert session.scalar(select(func.count(Job.id))) == job_count
    conflict = await client.post('/api/v1/runs', json={**payload, 'name': 'different'}, headers=headers)
    assert conflict.status_code == 409
    conflict = await client.post(url, files={'file': ('one.mgf', b'changed data')}, headers=headers)
    assert conflict.status_code == 409


@pytest.mark.anyio
async def test_concurrent_run_creation_with_same_key_is_idempotent(client, hierarchy):
    payload = {'experiment_id': hierarchy['experiment_id'], 'sample_id': hierarchy['sample_id'], 'name': 'concurrent batch'}
    headers = {'Idempotency-Key': str(uuid4())}
    results = await asyncio.gather(*(client.post('/api/v1/runs', json=payload, headers=headers) for _ in range(4)))
    assert [result.status_code for result in results] == [201] * 4
    assert len({result.json()['id'] for result in results}) == 1


@pytest.mark.anyio
async def test_server_path_batch_retry_and_independent_keys(client, hierarchy, import_root):
    source = import_root / 'batch.d'
    source.mkdir(exist_ok=True)
    (source / 'analysis.tdf').write_bytes(b'synthetic directory data')
    payload = {'experiment_id': hierarchy['experiment_id'], 'name': 'server acquisition'}
    headers = {'Idempotency-Key': str(uuid4())}
    run = (await client.post('/api/v1/runs', json=payload, headers=headers)).json()
    url = f'/api/v1/runs/{run["id"]}/artifacts/import'
    first = await client.post(url, json={'source_path': str(source)}, headers=headers)
    second = await client.post(url, json={'source_path': str(source)}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] == second.json()['id']
    different = await client.post('/api/v1/runs', json=payload, headers={'Idempotency-Key': str(uuid4())})
    assert different.status_code == 201
    assert different.json()['id'] != run['id']
    invalid = await client.post('/api/v1/runs', json=payload, headers={'Idempotency-Key': 'not-a-uuid'})
    assert invalid.status_code == 422


@pytest.mark.anyio
async def test_failed_source_can_be_retried_without_recreating_run(client, hierarchy, import_root):
    source = import_root / 'arriving.mgf'
    source.unlink(missing_ok=True)
    headers = {'Idempotency-Key': str(uuid4())}
    payload = {'experiment_id': hierarchy['experiment_id'], 'name': 'arriving'}
    first = await client.post('/api/v1/runs', json=payload, headers=headers)
    run_id = first.json()['id']
    url = f'/api/v1/runs/{run_id}/artifacts/import'
    assert (await client.post(url, json={'source_path': str(source)}, headers=headers)).status_code == 404
    source.write_text('BEGIN IONS\n100 10\nEND IONS')
    retry = await client.post('/api/v1/runs', json=payload, headers=headers)
    assert retry.json()['id'] == run_id
    assert (await client.post(url, json={'source_path': str(source)}, headers=headers)).status_code == 201

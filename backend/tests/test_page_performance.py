from sqlalchemy import event, select
from sqlalchemy.orm import raiseload

from spectarr.api import project_views
from spectarr.database import SessionLocal, engine
from spectarr.models import Artifact, Experiment, Project, Run, SdrfDocument, SdrfRow


def test_project_summaries_use_bounded_queries_without_loading_library_objects():
    with SessionLocal() as session:
        empty = Project(name="Empty")
        project = Project(name="Large")
        experiment = Experiment(project=project, name="Experiment")
        session.add_all([empty, project, experiment])
        for index in range(100):
            run = Run(experiment=experiment, name=f"Run {index}", source_class="open")
            session.add(run)
            for state, size in [("ready", 10), ("ready", 20), ("missing", 1000)]:
                session.add(Artifact(
                    run=run, role="source", state=state, format="mzml",
                    original_filename=f"{index}-{size}.mzML", storage_key=f"{index}-{size}",
                    byte_size=size, sha256="a" * 64,
                ))
        document = SdrfDocument(project=project, revision=3, source_filename="samples.tsv")
        session.add(document)
        session.add_all([SdrfRow(document=document, position=index, values=["sample"]) for index in range(20)])
        session.commit()

    queries = []

    def record_query(_connection, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", record_query)
    try:
        with SessionLocal() as session:
            projects = list(session.scalars(select(Project).options(raiseload("*")).order_by(Project.name)))
            result = project_views(session, projects)
            assert len(session.identity_map) == 2
    finally:
        event.remove(engine, "before_cursor_execute", record_query)

    assert len(queries) == 4
    assert result[0]["runCount"] == 0
    assert result[0]["sizeBytes"] == 0
    assert result[0]["sdrf"] is None
    assert result[1]["runCount"] == 100
    assert result[1]["sizeBytes"] == 3000
    assert result[1]["sdrf"] == {
        "status": "draft", "revision": 3, "row_count": 20, "source_filename": "samples.tsv",
    }

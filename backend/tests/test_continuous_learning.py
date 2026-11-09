from datetime import timedelta
from sqlalchemy import func, select
from app.db import SessionLocal, utcnow
from app.importer import ingest, reconcile
from app.ftp_scan import RemoteFile
from app.ml_service import queue_learning_jobs, run_training_job, sample_path
from app.models import Camera, ImageBuffer, TrainingSample, ModelVersion, Measurement, AnalysisResult, TrainingCollection
from app.training_store import collect_buffer, cleanup_deferred, retention_preview
from test_training import configure, product_image, upload, scene
from test_scan import ftp_tree, camera_connection, image
from app.training_collection import collect_training
from conftest import authenticate
from app.main import app
from fastapi.testclient import TestClient


def test_live_import_without_model_or_roi_has_no_count_limit(admin, container):
    cid = container['camera']['id']
    for i in range(65):
        product_image(container, level=i % 100, seed=2000+i, offset=i)
    with SessionLocal() as db:
        camera = db.get(Camera, cid)
        assert not camera.analysis_enabled and not camera.roi_json
        assert db.scalar(select(func.count()).select_from(ImageBuffer)) == 65
        assert db.scalar(select(func.count()).select_from(TrainingSample)) == 65
        assert not db.scalar(select(func.count()).select_from(Measurement))
        reconcile(db, camera)
        assert db.scalar(select(func.count()).select_from(ImageBuffer)) == 65
    base = f'/api/training/cameras/{cid}'
    page = admin.get(base + '/queue?limit=20').json()
    assert page['total_count'] == 65 and len(page['items']) == 20
    overview = admin.get(base).json()
    assert overview['latest_image']['sample_id'] == page['items'][0]['sample_id']
    assert overview['latest_image']['state'] == 'pending'
    assert overview['collection']['limit'] is None
    assert 'received_at' in page['items'][0]
    last = admin.get(base + '/queue?offset=60').json()['items'][-1]
    focused = admin.get(base + '/queue?limit=20&focus=' + last['id']).json()
    assert focused['offset'] == 60 and last['id'] in [row['id'] for row in focused['items']]


def test_enable_backfills_paused_images_and_pause_is_rechecked(admin, container):
    cid = container['camera']['id']; base=f'/api/training/cameras/{cid}'
    admin.put(base+'/learning', json={'enabled':False})
    product_image(container, seed=3000)
    with SessionLocal() as db:
        assert not db.scalar(select(TrainingSample))
        stale = db.get(Camera,cid)
        stale.learning_enabled = True
        db.rollback()
        assert collect_buffer(db,stale) == 0
    assert admin.put(base+'/learning', json={'enabled':True}).status_code == 200
    overview=admin.get(base).json()
    assert overview['latest_image']['state'] == 'pending'
    assert admin.get(base+'/queue').json()['pending_count'] == 1
    assert admin.put(base+'/learning', json={'enabled':True}).status_code == 200
    assert admin.get(base+'/queue').json()['pending_count'] == 1


def test_retention_uses_local_time_preserves_labels_uploads_and_latest(admin, container):
    cid=container['camera']['id']
    for i in range(4): product_image(container, level=20+i, seed=3100+i, offset=i)
    manual=upload(admin,container,10,seed=3200,label=False)
    with SessionLocal() as db:
        camera=db.get(Camera,cid)
        rows=list(db.scalars(select(ImageBuffer).order_by(ImageBuffer.captured_at)))
        for row in rows:
            row.imported_at=utcnow()-timedelta(days=8)
            db.get(TrainingSample,row.training_sample_id).created_at=utcnow()-timedelta(days=8)
        positive=db.get(TrainingSample,rows[1].training_sample_id)
        positive.approved=True; positive.target_fill_level=21; positive.queue_state='confirmed'
        negative=db.get(TrainingSample,rows[2].training_sample_id)
        negative.approved=True; negative.rejection_reason='lid_closed'; negative.queue_state='confirmed'
        db.get(TrainingSample,manual['id']).created_at=utcnow()-timedelta(days=100)
        latest=rows[-1].id; victim=rows[0].training_sample_id
        db.commit()
        assert retention_preview(db,cid,7) == 1
        reconcile(db,camera)
        assert list(db.scalars(select(ImageBuffer.id))) == [latest]
        assert db.get(TrainingSample,victim).queue_state == 'expired'
        assert not sample_path(db.get(TrainingSample,victim)).exists()
        assert sample_path(positive).exists() and sample_path(negative).exists()
        assert sample_path(db.get(TrainingSample,manual['id'])).exists()
    assert admin.get(f'/api/training/cameras/{cid}/queue').json()['pending_count'] == 2


def test_flat_backfill_and_explicit_discard_do_not_reimport(admin, container, ftp_tree):
    root,port,_=ftp_tree; cid=container['camera']['id']
    image(root,'flat.jpg',91,utcnow()-timedelta(minutes=5))
    with SessionLocal() as db:
        camera_connection(db,container,port,scan_mode='auto')
        assert collect_training(db,cid)==1
        row=db.scalar(select(TrainingSample)); sid=row.id
        assert db.scalar(select(ImageBuffer)) is None
        assert db.scalar(select(Measurement)) is None
    assert admin.post(f'/api/training/cameras/{cid}/queue/sample_{sid}/discard').status_code==200
    with SessionLocal() as db:
        db.get(TrainingCollection,cid).scanned_at=utcnow()-timedelta(hours=2)
        db.commit()
        assert collect_training(db,cid)==0
        assert db.scalar(select(func.count()).select_from(TrainingSample))==1


def test_manual_training_mode_cancels_only_queued_automatic_jobs(admin, container):
    configure(admin,container); cid=container['camera']['id']; base=f'/api/training/cameras/{cid}'
    for i in range(12): upload(admin,container,i*8,seed=4000+i,series=f'Serie {i%3}')
    queue_learning_jobs()
    with SessionLocal() as db:
        queued=db.scalar(select(ModelVersion)); old=queued.id
        assert queued.trigger=='automatic' and queued.status=='queued'
    assert admin.put(base+'/learning',json={'training_mode':'manual','image_retention_days':30}).status_code==200
    queue_learning_jobs()
    with SessionLocal() as db:
        assert db.get(ModelVersion,old).status=='cancelled'
        assert db.scalar(select(func.count()).select_from(ModelVersion))==1
        assert db.get(Camera,cid).image_retention_days==30
    response=admin.post('/api/models/train',json={'camera_id':cid,'managed':True})
    assert response.status_code==202,response.text
    assert response.json()['trigger']=='manual'
    assert run_training_job()
    assert admin.get(base).json()['summary']['has_active_recognition']
    with SessionLocal() as db:
        assert db.get(ModelVersion,response.json()['id']).active
    assert admin.post('/api/models/train',json={'camera_id':cid,'managed':True}).status_code==409


def test_learning_settings_and_preview_roles_validation_csrf(admin,container):
    cid=container['camera']['id']; base=f'/api/training/cameras/{cid}'
    for days in [0,366]:
        assert admin.put(base+'/learning',json={'image_retention_days':days}).status_code==422
    assert admin.get(base+'/retention-preview?days=1').json()=={'affected_unreviewed':0}
    with TestClient(app) as client:
        user=authenticate(client,'user@example.test','Leser-passwort-123')
        assert user.put(base+'/learning',json={'training_mode':'manual'}).status_code==403
        assert user.get(base+'/retention-preview?days=1').status_code==403
        assert user.post('/api/models/train',json={'camera_id':cid,'managed':True}).status_code==403
    admin.headers.pop('x-csrf-token')
    assert admin.put(base+'/learning',json={'training_mode':'manual'}).status_code==403


def test_manual_mode_keeps_running_job_and_restore_preserves_controls(admin,container):
    configure(admin,container);cid=container['camera']['id'];base=f'/api/training/cameras/{cid}'
    for i in range(12): upload(admin,container,i*8,seed=5000+i,series=f'Serie {i%3}')
    queue_learning_jobs()
    with SessionLocal() as db:
        model=db.scalar(select(ModelVersion));mid=model.id
        model.status='running';db.commit()
    assert admin.put(base+'/learning',json={'training_mode':'manual','image_retention_days':30}).status_code==200
    overview=admin.get(base).json()
    assert overview['summary']['training_running'] and not overview['summary']['can_train']
    with SessionLocal() as db:
        model=db.get(ModelVersion,mid); assert model.status=='running'
        model.status='failed';db.commit()
    point=admin.post(base+'/restore-points').json()['id']
    admin.put(base+'/learning',json={'training_mode':'automatic','image_retention_days':7})
    response=admin.post(base+f'/restore-points/{point}/restore')
    assert response.status_code==200,response.text
    overview=admin.get(base).json()
    assert overview['training_mode']=='manual' and overview['image_retention_days']==30


def test_late_ftp_image_is_kept_after_more_than_three_imports(container,ftp_tree):
    from app.importer import import_camera
    root,port,_=ftp_tree; now=utcnow()
    for i in range(4): image(root,f'{i}.jpg',30+i*30,now-timedelta(minutes=4-i))
    with SessionLocal() as db:
        camera=camera_connection(db,container,port,scan_mode='recursive')
        assert import_camera(db,camera,{})==4
        image(root,'late.jpg',177,now-timedelta(minutes=20))
        assert import_camera(db,camera,{})==1
        assert db.scalar(select(func.count()).select_from(ImageBuffer))==5
        assert db.scalar(select(func.count()).select_from(TrainingSample))==5


def test_new_analysis_does_not_hide_confirmed_reference(admin,container):
    from test_training import train_ready
    from app.ml_service import analyze_buffer
    model,_=train_ready(admin,container)
    assert admin.post(f"/api/models/{model['id']}/activate").status_code==200
    row=upload(admin,container,60,seed=6600)
    product_image(container,60,6600)
    with SessionLocal() as db:
        analyze_buffer(db,db.get(Camera,container['camera']['id']))
        result=db.scalar(select(AnalysisResult))
        assert result.review_sample_id==row['id']
        assert not result.review_visible
    queue=admin.get(f"/api/training/cameras/{container['camera']['id']}/queue?tab=confirmed&limit=100").json()
    assert row['id'] in [item['sample_id'] for item in queue['items']]

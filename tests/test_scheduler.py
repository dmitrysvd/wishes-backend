from app.scheduler import run_job, start_scheduler


def test_run_job_success(mocker):
    mock_func = mocker.Mock()
    mock_logger = mocker.patch('app.scheduler.logger')

    run_job(mock_func, 'test_job')

    mock_func.assert_called_once()
    mock_logger.info.assert_called_with('Running job: test_job')


def test_run_job_exception(mocker):
    mock_func = mocker.Mock(side_effect=Exception('error'))
    mock_logger = mocker.patch('app.scheduler.logger')

    # Should not raise
    run_job(mock_func, 'test_job')

    mock_func.assert_called_once()
    mock_logger.exception.assert_called_with('Error running job test_job')


def test_start_scheduler(mocker):
    # Mock BlockingScheduler
    mock_sched_class = mocker.patch('app.scheduler.BlockingScheduler')
    mock_sched = mock_sched_class.return_value

    # Mock logger to avoid actual logging during tests if needed
    mocker.patch('app.scheduler.logger')

    start_scheduler()

    assert mock_sched.add_job.call_count == 3
    mock_sched.start.assert_called_once()


def test_start_scheduler_interrupt(mocker):
    mock_sched_class = mocker.patch('app.scheduler.BlockingScheduler')
    mock_sched = mock_sched_class.return_value
    mock_sched.start.side_effect = KeyboardInterrupt()

    mock_logger = mocker.patch('app.scheduler.logger')

    start_scheduler()

    mock_logger.info.assert_any_call('Scheduler stopped')


def test_scheduler_script_execution(mocker):
    import os
    import runpy

    from app import scheduler

    # Patch the start method on the class to prevent blocking
    mock_start = mocker.patch('apscheduler.schedulers.blocking.BlockingScheduler.start')
    mocker.patch('app.scheduler.logger')

    script_path = os.path.abspath(scheduler.__file__)
    runpy.run_path(script_path, run_name='__main__')
    assert mock_start.called

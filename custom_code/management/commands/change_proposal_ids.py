from datetime import timedelta
from dateutil.parser import parse
from django.core.management.base import BaseCommand
from django.utils import timezone
from tom_observations.models import ObservationRecord, DynamicCadence
import logging

logger = logging.getLogger(__name__)

RETRY_STRATEGIES = ('SnexRetryFailedObservationsStrategy', 'SnexRetryUntilDeadlineStrategy')


class Command(BaseCommand):

    help = ('Changes proposal IDs for current ObservationRecords associated with active DynamicCadences '
            '(useful after semester changes or key projects end). With --semester-start, only switches '
            'sequences whose next observing window would start on or after that date, so it can be run '
            'repeatedly from cron ahead of a semester boundary.')

    def add_arguments(self, parser):
        parser.add_argument('--oldid', help='The old ID to change')
        parser.add_argument('--newid', help='The new ID to change to')
        parser.add_argument('--semester-start', help='Only switch sequences whose next window starts on or after this date')
        parser.add_argument('--dry-run', action='store_true', help='Report what would change without saving')

    def next_window_start(self, cadence, last_obs, now):
        if not last_obs.terminal or last_obs.status == 'CANCELED':
            return None
        if last_obs.failed:
            return now
        if cadence.cadence_strategy in RETRY_STRATEGIES:
            return None
        cadence_frequency = cadence.cadence_parameters.get('cadence_frequency')
        if cadence_frequency is None:
            return None
        scheduled_end = last_obs.scheduled_end
        if not scheduled_end:
            end_value = last_obs.parameters.get('end')
            if not end_value:
                return now
            scheduled_end = parse(end_value)
        if timezone.is_naive(scheduled_end):
            scheduled_end = timezone.make_aware(scheduled_end)
        return max(scheduled_end + timedelta(hours=cadence_frequency), now)

    def handle(self, *args, **options):
        if not options['oldid'] or not options['newid']:
            logger.error('You need to provide both an old ID and a new ID')
            return None

        semester_start = None
        if options['semester_start']:
            semester_start = parse(options['semester_start'])
            if timezone.is_naive(semester_start):
                semester_start = timezone.make_aware(semester_start)

        now = timezone.now()
        record_ids_to_update = set()

        for cadence in DynamicCadence.objects.filter(active=True).select_related('observation_group'):
            last_obs = cadence.observation_group.observation_records.order_by('-created').first()
            if not last_obs or last_obs.parameters.get('proposal') != options['oldid']:
                continue
            if semester_start:
                window_start = self.next_window_start(cadence, last_obs, now)
                if window_start is None or window_start < semester_start:
                    continue
            record_ids_to_update.add(last_obs.id)
            for pending in cadence.observation_group.observation_records.filter(status='PENDING'):
                if pending.parameters.get('proposal') == options['oldid']:
                    record_ids_to_update.add(pending.id)

        records_to_update = ObservationRecord.objects.filter(id__in=record_ids_to_update)

        for rec in records_to_update:
            if options['dry_run']:
                self.stdout.write(f'Would update ObservationRecord {rec.id} ({rec.target.name}) from {options["oldid"]} to {options["newid"]}')
                continue
            rec.parameters['proposal'] = options['newid']
            rec.save()

        action = 'Would update' if options['dry_run'] else 'Updated'
        self.stdout.write(f'{action} {records_to_update.count()} record(s) from {options["oldid"]} to {options["newid"]}')
        if not options['dry_run']:
            logger.info('Finished updating current sequences from {} to {}'.format(options['oldid'], options['newid']))

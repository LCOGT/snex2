import logging
import traceback

from tom_nonlocalizedevents.alertstream_handlers.igwn_event_handler import handle_igwn_message
from gw.find_galaxies import generate_galaxy_list
from gw.models import GWFollowupGalaxy
from tom_common.hooks import run_hook

logger = logging.getLogger(__name__)


def handle_igwn_message_with_galaxies(message, metadata):

    nonlocalizedevent, event_sequence = handle_igwn_message(message, metadata)
    if nonlocalizedevent is None:
        return None, None

    if event_sequence is None:
        for sequence in nonlocalizedevent.sequences.all():
            run_hook('cancel_gw_obs', galaxy_ids=[], sequence_id=sequence.id)
        return nonlocalizedevent, None

    localization = event_sequence.localization
    if localization is None or GWFollowupGalaxy.objects.filter(eventlocalization=localization).exists():
        return nonlocalizedevent, event_sequence
    try:
        generate_galaxy_list(localization)
    except Exception as e:
        logger.error('Could not generate galaxy list with exception {}'.format(e))
        logger.error(traceback.format_exc())

    return nonlocalizedevent, event_sequence

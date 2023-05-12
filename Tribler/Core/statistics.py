import logging
import binascii

from Tribler.dispersy.util import blocking_call_on_reactor_thread

from Tribler.Core.Utilities.misc_utils import compute_ratio


DATA_NONE = u"None"


class TriblerStatistics(object):

    def __init__(self, session):
        """
        Constructor.
        :param session: The Tribler session.
        """
        self._logger = logging.getLogger(self.__class__.__name__)
        self._session = session

    @blocking_call_on_reactor_thread
    def dump_statistics(self):
        """
        Dumps all statistics.
        :return: A dictionary of data.
        """
        dispersy = self._session.get_dispersy_instance()
        if dispersy is None:
            # we use critical here because whoever calls this function wants to get statistics, so it
            # should be at least an error if we can't get dispersy.
            self._logger.critical(u"dispersy instance not found.")

        dispersy.statistics.update()

        return {u'communities': self._create_community_data(dispersy)}

    def _create_community_data(self, dispersy):
        """
        Creates a dictionary of community statistics data.
        :param dispersy: The dispersy instance.
        :return: A dictionary of community statistics data.
        """
        community_data_dict = {}

        for community in dispersy.statistics.communities:
            candidate_list = None
            if community.dispersy_enable_candidate_walker or \
                        community.dispersy_enable_candidate_walker_responses:
                candidate_count = u"%d " % len(community.candidates)
                candidate_list = [
                    (
                        f"{global_time}",
                        u"%s:%s" % lan,
                        u"%s:%s" % wan,
                        f"{binascii.hexlify(mid)}" if mid else DATA_NONE,
                    )
                    for lan, wan, global_time, mid in community.candidates
                ]
                candidate_list.sort()
            elif community.candidates:
                candidate_count = u"%d*" % len(community.candidates)
            else:
                candidate_count = u"-"

            database_list = []
            if community.database:
                database_str = u"%d packets" % sum(community.database.itervalues())
                database_list.extend(
                    (f"{count}", f"{name}")
                    for name, count in sorted(
                        community.database.iteritems(), key=lambda tup: tup[1]
                    )
                )
            else:
                database_str = u"? packets"

            median_global_time = u"%d (%d difference)" % (
                community.acceptable_global_time
                - community.dispersy_acceptable_global_time_range,
                community.acceptable_global_time
                - community.global_time
                - community.dispersy_acceptable_global_time_range,
            )
            community_data = {
                u"Identifier": f"{community.hex_cid}",
                u"Member": f"{community.hex_mid}",
                u"Classification": f"{community.classification}",
                u"Database id": f"{community.database_id}",
                u"Global time": f"{community.global_time}",
                u"Median global time": f"{median_global_time}",
                u"Acceptable range": f"{community.dispersy_acceptable_global_time_range}",
                u"Sync bloom created": f"{community.sync_bloom_new}",
                u"Sync bloom reused": f"{community.sync_bloom_reuse}",
                u"Sync bloom skipped": f"{community.sync_bloom_skip}",
                u"Candidates": f"{candidate_count}",
                u"Candidate_list": candidate_list,
                u"Database": database_str,
                u"Database_list": database_list,
                u"Packets Created": f"{community.msg_statistics.created_count}",
                u"Packets Sent": f"{compute_ratio(community.msg_statistics.outgoing_count, community.msg_statistics.outgoing_count + community.msg_statistics.total_received_count)}",
                u"Packets Received": f"{compute_ratio(community.msg_statistics.total_received_count, community.msg_statistics.outgoing_count + community.msg_statistics.total_received_count)}",
                u"Packets Success": compute_ratio(
                    community.msg_statistics.success_count,
                    community.msg_statistics.total_received_count,
                ),
                u"Packets Dropped": compute_ratio(
                    community.msg_statistics.drop_count,
                    community.msg_statistics.total_received_count,
                ),
                u"Packets Delayed Sent": compute_ratio(
                    community.msg_statistics.delay_send_count,
                    community.msg_statistics.total_received_count,
                ),
                u"Packets Delayed Received": compute_ratio(
                    community.msg_statistics.delay_received_count,
                    community.msg_statistics.total_received_count,
                ),
                u"Packets Delayed Success": compute_ratio(
                    community.msg_statistics.delay_success_count,
                    community.msg_statistics.delay_received_count,
                ),
                u"Packets Delayed Timeout": compute_ratio(
                    community.msg_statistics.delay_timeout_count,
                    community.msg_statistics.delay_received_count,
                ),
                u"Statistics": self._get_community_rawinfo(community),
            }

            key = f"<{community.classification}>: {community.hex_cid}"
            community_data_dict[key] = community_data

        return community_data_dict

    def _get_community_rawinfo(self, community):
        categories = (u"attachment", u"endpoint_recv", u"endpoint_send",
                      u"walk_failure_dict", u"incoming_intro_dict", u"outgoing_intro_dict")
        msg_categories = (u"success", u"drop", u"created", u"delay", u"outgoing")
        ip_categories = (u"walk_failure_dict", u"incoming_intro_dict", u"outgoing_intro_dict")

        raw_info = {}
        for category in categories:
            if getattr(community, category, None):
                raw_info[category] = {}
                for key, val in getattr(community, category).items():
                    raw_info[category][key] = unicode(val)

        for category in msg_categories:
            dict_name = f"{category}_dict"
            if getattr(community.msg_statistics, dict_name, None):
                raw_info[category] = {}
                for key, val in getattr(community.msg_statistics, dict_name).items():
                    raw_info[category][key] = unicode(val)

        return raw_info

from math import sqrt


def sort_torrent_fulltext(data_set):
    """ sorts a given list of torrents using fulltext sorting.
    :param data_set: The given list of data.
    """
    # TODO(lipu): This has to be decoupled from GuiTuple
    norm_num_seeders = normalize_data_dict(data_set, 'num_seeders', 'infohash')
    norm_neg_votes = normalize_data_dict(data_set, 'neg_votes', 'infohash')
    norm_subscriptions = normalize_data_dict(data_set, 'subscriptions', 'infohash')

    for data in data_set:
        score = 0.8 * norm_num_seeders[data.get('infohash')]\
            - 0.1 * norm_neg_votes[data.get('infohash')]\
            + 0.1 * norm_subscriptions[data.get('infohash')]
        data.get('relevance_score')[-1] = score

    data_set.sort(key=lambda d: d.get('relevance_score'), reverse=True)


def normalize_data_dict(data_set, key_to_normalize, key_for_index):
    """ Normalizes a list of data.
    :param data_set: The given list of data.
    :param key_to_normalize: The key of the data field that needs to be normalized.
    :param key_for_index: The key for index.
    :return: A dictionary with key_for_index as keys and the normalized data as values.
    """
    assert isinstance(data_set, list), f"data_set is not list: {type(data_set)}"
    assert isinstance(
        key_to_normalize, basestring
    ), f"key_to_normalize is not basestring: {type(key_to_normalize)}"
    assert isinstance(
        key_for_index, basestring
    ), f"key_for_index is not basestring: {type(key_for_index)}"

    total = sum((data.get(key_to_normalize, 0) or 0) for data in data_set)
    mean = total / len(data_set) if len(data_set) > 0 else 0
    total_sum = 0
    for data in data_set:
        temp = (data.get(key_to_normalize, 0) or 0) - mean
        temp *= temp
        total_sum += temp

    dev = total_sum / (len(data_set) - 1) if len(data_set) > 1 else 0
    std_dev = sqrt(dev)

    return {
        data.get(key_for_index): ((data.get(key_to_normalize, 0) or 0) - mean)
        / std_dev
        if std_dev > 0
        else 0
        for data in data_set
    }
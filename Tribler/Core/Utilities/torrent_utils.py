import os
import libtorrent


def create_torrent_file(file_path_list, params, callback=None):
    fs = libtorrent.file_storage()

    # filter all non-files
    file_path_list = [file for file in file_path_list if os.path.isfile(file)]

    # get the directory where these files are in. If there are multiple files, take the common directory they are in
    if len(file_path_list) == 1:
        base_path = os.path.split(file_path_list[0])[0]
    else:
        base_path = os.path.dirname(os.path.abspath(os.path.commonprefix(file_path_list)))

    # the base_dir directory is the parent directory of the base_path and is passed to the set_piece_hash method
    base_dir = os.path.split(base_path)[0]

    for full_file_path in file_path_list:
        filename = os.path.basename(full_file_path)
        filename = os.path.join(base_path[len(base_dir) + 1:], filename)
        fs.add_file(filename, os.path.getsize(full_file_path))

    piece_size = params['piece length'] if params.get('piece length') else 0
    flags = libtorrent.create_torrent_flags_t.optimize | libtorrent.create_torrent_flags_t.calculate_file_hashes
    torrent = libtorrent.create_torrent(fs, piece_size=piece_size, flags=flags)
    if params.get('comment'):
        torrent.set_comment(params['comment'])
    if params.get('created by'):
        torrent.set_creator(params['created by'])
    # main tracker
    if params.get('announce'):
        torrent.add_tracker(params['announce'])
    # tracker list
    if params.get('announce-list'):
        for tier, _ in enumerate(params['announce-list'], start=1):
            torrent.add_tracker(params['announce'], tier=tier)
    # DHT nodes
    if params.get('nodes'):
        for node in params['nodes']:
            torrent.add_node(*node)
    # HTTP seeding
    if params.get('httpseeds'):
        torrent.add_http_seed(params['httpseeds'])

    if len(file_path_list) == 1 and params.get('urllist', False):
        torrent.add_url_seed(params['urllist'])

    # read the files and calculate the hashes
    libtorrent.set_piece_hashes(torrent, base_dir)

    t1 = torrent.generate()
    torrent = libtorrent.bencode(t1)

    postfix = '.torrent'

    torrent_file_name = os.path.join(base_path, t1['info']['name'] + postfix)
    with open(torrent_file_name, 'wb') as f:
        f.write(torrent)

    if callback is not None:
        result = {'success': True,
                  'base_path': base_path,
                  'base_dir': base_dir,
                  'torrent_file_path': torrent_file_name}
        callback(result)


def get_info_from_handle(handle):
    # In libtorrent 0.16.18, the torrent_handle.torrent_file method is not available.
    # this method checks whether the torrent_file method is available on a given handle.
    # If not, fall back on the deprecated get_torrent_info
    if hasattr(handle, 'torrent_file'):
        return handle.torrent_file()
    return handle.get_torrent_info()

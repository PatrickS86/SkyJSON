# ONLY RELEVANT PART CHANGED

def get_versions_simple() -> Dict[str, Any]:
    local_version = get_local_file_version()

    fetch_ok = False
    fetch_error = ''
    remote_version = 'unknown'

    if (BASE_DIR / '.git').exists():
        fetch = run_cmd(['git', 'fetch', '--tags', 'origin'])
        fetch_ok = fetch['ok']
        fetch_error = fetch['stderr'] or fetch['stdout']
        if fetch_ok:
            remote_version = get_remote_version() or 'unknown'

    update_available = (
        fetch_ok
        and remote_version not in ('unknown', '', None)
        and remote_version != local_version
    )

    return {
        'local': local_version,
        'remote': remote_version,
        'update_available': update_available,
        'fetch_ok': fetch_ok,
        'fetch_error': fetch_error,
        'status_text': 'Update available' if update_available else 'No new updates',
    }

def get_available_apps():
    apps = []

    try:
        from .fortune import main as fortune_main
        apps.append({
            'name': '오늘의 운세',
            'description': '동양 철학(사주)를 통한 오늘의 운세를 가볍게 확인하세요.',
            'module': 'fortune',
            'main': fortune_main.main
        })
    except ImportError:
        pass

    return apps

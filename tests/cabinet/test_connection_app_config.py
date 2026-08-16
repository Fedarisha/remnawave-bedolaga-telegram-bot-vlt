from app.cabinet.routes.subscription_modules.status import _filter_referenced_svg_library


def test_filter_referenced_svg_library_keeps_nested_platform_icons() -> None:
    svg_library = {
        'platform-icon': {'svgString': '<svg />'},
        'app-icon': {'svgString': '<svg />'},
        'step-icon': {'svgString': '<svg />'},
        'button-icon': {'svgString': '<svg />'},
        'unused-icon': {'svgString': '<svg />'},
    }
    platforms = {
        'android': {
            'svgIconKey': 'platform-icon',
            'apps': [
                {
                    'svgIconKey': 'app-icon',
                    'blocks': [
                        {
                            'svgIconKey': 'step-icon',
                            'buttons': [{'svgIconKey': 'button-icon'}],
                        }
                    ],
                }
            ],
        }
    }

    result = _filter_referenced_svg_library(svg_library, platforms)

    assert set(result) == {'platform-icon', 'app-icon', 'step-icon', 'button-icon'}


def test_filter_referenced_svg_library_ignores_invalid_or_missing_keys() -> None:
    svg_library = {'kept': '<svg />', 'unused': '<svg />'}
    platforms = {
        'windows': {
            'apps': [
                {'svgIconKey': 'kept'},
                {'svgIconKey': None},
                {'svgIconKey': 42},
            ]
        }
    }

    assert _filter_referenced_svg_library(svg_library, platforms) == {'kept': '<svg />'}


def test_filter_referenced_svg_library_drops_oversized_decorative_bundle() -> None:
    svg_library = {
        'large-icon': {'svgString': '<svg>' + ('x' * 8_100) + '</svg>'},
    }
    platforms = {
        'ios': {
            'apps': [{'svgIconKey': 'large-icon'}],
        }
    }

    assert _filter_referenced_svg_library(svg_library, platforms) == {}

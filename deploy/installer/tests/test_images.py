"""E8: the ways preparing images can fail, each before or instead of a wrong image."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fake_host  # noqa: E402
from app_installer import apps, images, platform_file  # noqa: E402
from fake_host import FakeHost, write_images  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class WrongLabelHost(FakeHost):
    """A host whose localhost/platform-proxy image is some other image, without the nginx label."""

    def answer(self, argv, input):
        if argv[:3] == ['podman', 'image', 'inspect'] and '--format' not in argv:
            return 0, '[{"Labels":{"maintainer":"someone"}}]'
        return super().answer(argv, input)


class ImageErrorTests(unittest.TestCase):
    def test_a_proxy_image_without_the_nginx_label_is_refused_in_both_modes(self):
        for mode in ('build', 'offline'):
            with self.subTest(mode), WrongLabelHost(), tempfile.TemporaryDirectory() as bundle:
                write_images(bundle, platform_file.checkout())
                with self.assertRaisesRegex(RuntimeError, 'does not identify nginx.*refresh_images=true'):
                    images.prepare_shared(ROOT, mode, bundle, False, platform_file.checkout())

    def test_offline_refuses_a_refresh_or_a_missing_bundle_before_any_command(self):
        for bundle, refresh in (('/bundle', True), ('', False)):
            with self.subTest(bundle=bundle, refresh=refresh), FakeHost() as host:
                with self.assertRaisesRegex(ValueError, 'requires bundle_directory and forbids refresh_images'):
                    images.prepare(ROOT, 'offline', bundle, refresh, app=platform_file.checkout().apps[0])
                self.assertEqual(host.calls, [])
        with FakeHost() as host, self.assertRaisesRegex(ValueError, 'build or offline'):
            images.prepare(ROOT, 'online', app=platform_file.checkout().apps[0])
        self.assertEqual(host.calls, [])

    def test_a_bundle_without_an_image_archive_names_it_and_loads_nothing(self):
        with tempfile.TemporaryDirectory() as bundle, FakeHost(images_present=False) as host:
            write_images(bundle, platform_file.checkout(), archives=False)
            with self.assertRaisesRegex(FileNotFoundError, 'no image archive images/todo-backend-m12.tar'):
                images.prepare(ROOT, 'offline', bundle, app=platform_file.checkout().apps[0])
            self.assertFalse(host.ran('podman', 'load'))

    def test_an_older_image_under_the_same_tag_is_replaced_by_the_bundles(self):
        # The fix ships under the same tag: the host's image has another ID, so the bundle's is loaded.
        app = platform_file.checkout().apps[0]
        reference = app.image('backend')
        with tempfile.TemporaryDirectory() as bundle, FakeHost() as host:
            write_images(bundle, platform_file.checkout())
            host.image_ids[reference] = 'sha256:an-older-build'
            changed = images.prepare(ROOT, 'offline', bundle, app=app)
            self.assertEqual(changed, {'backend': True, 'frontend': False})
            self.assertEqual([Path(argv[-1]).name for argv in host.ran('podman', 'load')], [app.image_archive('backend')])
            self.assertEqual(images.image_id(reference), fake_host.image_id(reference))

    def test_a_loaded_image_with_another_id_than_the_bundle_says_stops_the_install(self):
        app = platform_file.checkout().apps[0]
        with tempfile.TemporaryDirectory() as bundle, FakeHost(images_present=False):
            write_images(bundle, platform_file.checkout())
            fake_host.write_archive(Path(bundle) / 'images' / app.image_archive('backend'), app.image('backend'),
                                    'sha256:something-else')
            with self.assertRaisesRegex(RuntimeError, r'has the ID sha256:something-else, not .* build the bundle again'):
                images.prepare(ROOT, 'offline', bundle, app=app)

    def test_a_bundle_without_image_ids_or_an_image_in_them_is_refused(self):
        app = platform_file.checkout().apps[0]
        with tempfile.TemporaryDirectory() as bundle, FakeHost() as host:
            with self.assertRaisesRegex(FileNotFoundError, 'has no images/image-ids.json'):
                images.prepare(ROOT, 'offline', bundle, app=app)
            write_images(bundle, platform_file.checkout().select(['notes']))
            with self.assertRaisesRegex(ValueError, f'image-ids.json has no ID for {app.image("backend")}'):
                images.prepare(ROOT, 'offline', bundle, app=app)
            self.assertEqual(host.ran('podman', 'load'), [])

    def test_a_present_image_is_kept_offline_without_its_archive(self):
        # An image already on the host with the bundle's ID is not loaded again, so its archive is not needed.
        with tempfile.TemporaryDirectory() as bundle, FakeHost() as host:
            write_images(bundle, platform_file.checkout(), archives=False)
            self.assertEqual(set(images.prepare(ROOT, 'offline', bundle, app=platform_file.checkout().apps[0]).values()), {False})
            self.assertFalse(host.ran('podman', 'load'))


class BuildTests(unittest.TestCase):
    def test_an_app_image_named_proxy_is_not_the_shared_proxy(self):
        # Only the shared nginx image must carry the nginx label, whatever an app calls its images.
        app = apps.App(name='shop', hostname='shop.test', keycloak_client='shop-frontend',
                       images=(apps.AppImage(name='proxy', context='.'),))
        with WrongLabelHost(images_present=False) as host:
            self.assertEqual(images.prepare('/project', 'build', app=app), {'proxy': True})
        self.assertFalse(host.ran('podman', 'image', 'inspect'))

    def test_each_image_builds_from_its_declared_context_and_containerfile(self):
        app = apps.App(name='shop', hostname='shop.test', keycloak_client='shop-frontend',
                       images=(apps.AppImage(name='backend', context='.', containerfile='shop-backend/Containerfile'),
                               apps.AppImage(name='site', context='../help')))
        with FakeHost(images_present=False) as host:
            images.prepare('/project', 'build', app=app)
        builds = host.ran('podman', 'build')
        self.assertEqual([build[-5:] for build in builds], [
            ['--file', '/project/shop-backend/Containerfile', '--tag', 'localhost/shop-backend:m12', '/project'],
            ['--file', '/project/../help/Containerfile', '--tag', 'localhost/shop-site:m12', '/project/../help']])
        # Today's apps build exactly as before: the repository is the context of every Containerfile.
        todo = platform_file.checkout().apps[0]
        self.assertEqual([(image.context, image.containerfile) for image in images.image_list(todo)],
                         [('.', 'todo-backend/Containerfile'), ('.', 'todo-frontend/Containerfile')])



class GroupTests(unittest.TestCase):
    """Every image of a platform, from a bundle on a standby or into a bundle on a build host."""

    def every_image(self, platform):
        return {image.reference: image for app in platform.apps for image in images.image_list(app)} | {
            image.reference: image for image in images.shared_images(platform)}

    def test_a_standby_loads_every_missing_image_from_the_bundle_and_never_builds_or_pulls(self):
        platform = platform_file.checkout()
        with tempfile.TemporaryDirectory() as bundle, FakeHost(images_present=False) as host:
            write_images(bundle, platform)
            self.assertTrue(images.prepare_offline_group(bundle, platform))
            loaded = [Path(argv[-1]).name for argv in host.ran('podman', 'load')]
            self.assertEqual(sorted(loaded), sorted(image.archive for image in self.every_image(platform).values()))
            self.assertEqual(host.ran('podman', 'build') + host.ran('podman', 'pull'), [])
        # On a host without the images, a missing archive stops the load and names it.
        with tempfile.TemporaryDirectory() as bundle, FakeHost(images_present=False):
            write_images(bundle, platform)
            (Path(bundle) / 'images' / platform.apps[0].image_archive('backend')).unlink()
            with self.assertRaisesRegex(FileNotFoundError, platform.apps[0].image_archive('backend')):
                images.prepare_offline_group(bundle, platform)

    def test_images_that_are_present_are_not_loaded_again(self):
        with tempfile.TemporaryDirectory() as bundle, FakeHost() as host:
            write_images(bundle, platform_file.checkout(), archives=False)
            self.assertFalse(images.prepare_offline_group(bundle, platform_file.checkout()))
            self.assertEqual(host.ran('podman', 'load'), [])

    def test_a_bundle_build_makes_and_saves_each_distinct_image_once(self):
        platform = platform_file.checkout()
        # PostgreSQL serves three databases and nginx every app; each is still made and saved once.
        with tempfile.TemporaryDirectory() as destination, FakeHost(images_present=False) as host:
            images.build_and_export(ROOT, destination, platform)
            expected = self.every_image(platform)
            saved = [argv[argv.index('--output') + 1] for argv in host.ran('podman', 'save')]
            self.assertEqual(sorted(Path(path).name for path in saved),
                             sorted(image.archive for image in expected.values()))
            self.assertTrue(all(Path(path).parent == Path(destination) for path in saved))
            built = [argv[argv.index('--tag') + 1] for argv in host.ran('podman', 'build')]
            pulled = [argv[-1] for argv in host.ran('podman', 'pull')]
            self.assertEqual(sorted(built + pulled), sorted(expected))
            # PostgreSQL is pulled; everything else is built from its Containerfile, with --pull.
            self.assertEqual(pulled, [apps.KEYCLOAK_DATABASE.image])
            self.assertTrue(all('--pull' in argv for argv in host.ran('podman', 'build')))
            # Each image's ID, for an offline install to compare with the host's.
            self.assertEqual(json.loads((Path(destination) / images.IMAGE_IDS).read_text()),
                             {reference: fake_host.image_id(reference) for reference in expected})


if __name__ == '__main__':
    unittest.main()

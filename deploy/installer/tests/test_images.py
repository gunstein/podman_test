"""E8: the ways preparing images can fail, each before or instead of a wrong image."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, images, platform_file  # noqa: E402
from fake_host import FakeHost  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class WrongLabelHost(FakeHost):
    """A host whose localhost/platform-proxy image is some other image, without the nginx label."""

    def answer(self, argv, input):
        if argv[:3] == ['podman', 'image', 'inspect']:
            return 0, '[{"Labels":{"maintainer":"someone"}}]'
        return super().answer(argv, input)


class ImageErrorTests(unittest.TestCase):
    def test_a_proxy_image_without_the_nginx_label_is_refused_in_both_modes(self):
        for mode in ('build', 'offline'):
            with self.subTest(mode), WrongLabelHost(), tempfile.TemporaryDirectory() as bundle:
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
            (Path(bundle) / 'images').mkdir()
            with self.assertRaisesRegex(FileNotFoundError, 'no image archive images/todo-backend-m12.tar'):
                images.prepare(ROOT, 'offline', bundle, app=platform_file.checkout().apps[0])
            self.assertFalse(host.ran('podman', 'load'))

    def test_a_present_image_is_kept_offline_without_its_archive(self):
        # An image already on the host is not loaded again, so its archive is not needed.
        with tempfile.TemporaryDirectory() as bundle, FakeHost() as host:
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
            (Path(bundle) / 'images').mkdir()
            for image in self.every_image(platform).values():
                (Path(bundle) / 'images' / image.archive).write_text('archive')
            self.assertTrue(images.prepare_offline_group(bundle, platform))
            loaded = [Path(argv[-1]).name for argv in host.ran('podman', 'load')]
            self.assertEqual(sorted(loaded), sorted(image.archive for image in self.every_image(platform).values()))
            self.assertEqual(host.ran('podman', 'build') + host.ran('podman', 'pull'), [])
            # A missing archive stops the load and names it.
            (Path(bundle) / 'images' / platform.apps[0].image_archive('backend')).unlink()
            with self.assertRaisesRegex(FileNotFoundError, platform.apps[0].image_archive('backend')):
                images.prepare_offline_group(bundle, platform)

    def test_images_that_are_present_are_not_loaded_again(self):
        with tempfile.TemporaryDirectory() as bundle, FakeHost() as host:
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


if __name__ == '__main__':
    unittest.main()

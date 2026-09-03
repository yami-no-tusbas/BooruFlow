import unittest

from booruflow.cli.similar_artists import parser


class SimilarArtistsCliTests(unittest.TestCase):
    def test_commands_and_site_scoped_artist_identity(self):
        args = parser().parse_args(["artist", "gelbooru:butterchalk", "--backend", "openclip"])
        self.assertEqual((args.artist.site, args.artist.tag), ("gelbooru", "butterchalk"))
        self.assertEqual(args.backend, "openclip")

    def test_encode_scope_is_explicit_and_defaults_to_production_database(self):
        args = parser().parse_args(["encode-missing", "--backend", "openclip", "--item", "42"])
        self.assertEqual(args.item, 42)
        self.assertEqual(str(args.database).replace("\\", "/"), "var/state/image_analysis.sqlite")


if __name__ == "__main__": unittest.main()

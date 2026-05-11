import unittest

from reporting import BufferedExcelWriter


class BufferedExcelWriterTests(unittest.TestCase):
    def test_batches_rows_by_path_and_flushes_on_close(self):
        writes = []

        def append_func(path, rows):
            writes.append((path, list(rows)))

        writer = BufferedExcelWriter(flush_interval=60, max_batch_rows=10, append_func=append_func)
        writer.append("trades.xlsx", [{"Trade No": 1}])
        writer.append("trades.xlsx", [{"Trade No": 2}])
        writer.append("candles.xlsx", [{"Close": 100}])

        self.assertTrue(writer.close(timeout=5))

        self.assertEqual(
            writes,
            [
                ("trades.xlsx", [{"Trade No": 1}, {"Trade No": 2}]),
                ("candles.xlsx", [{"Close": 100}]),
            ],
        )
        self.assertEqual(writer.enqueued_rows, 3)
        self.assertEqual(writer.flushed_rows, 3)
        self.assertEqual(writer.errors, [])

    def test_flush_drains_without_closing_writer(self):
        writes = []

        def append_func(path, rows):
            writes.append((path, list(rows)))

        writer = BufferedExcelWriter(flush_interval=60, max_batch_rows=10, append_func=append_func)
        writer.append("trades.xlsx", [{"Trade No": 1}])

        self.assertTrue(writer.flush(timeout=5))
        writer.append("trades.xlsx", [{"Trade No": 2}])
        self.assertTrue(writer.close(timeout=5))

        self.assertEqual(
            writes,
            [
                ("trades.xlsx", [{"Trade No": 1}]),
                ("trades.xlsx", [{"Trade No": 2}]),
            ],
        )

    def test_closed_writer_falls_back_to_direct_write(self):
        writes = []

        def append_func(path, rows):
            writes.append((path, list(rows)))

        writer = BufferedExcelWriter(flush_interval=60, max_batch_rows=10, append_func=append_func)
        self.assertTrue(writer.close(timeout=5))
        writer.append("trades.xlsx", [{"Trade No": 1}])

        self.assertEqual(writes, [("trades.xlsx", [{"Trade No": 1}])])


if __name__ == "__main__":
    unittest.main()

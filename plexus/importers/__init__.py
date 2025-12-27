"""
Plexus Data Importers

Import data from various formats into Plexus:
- ROS bags (.bag, .mcap, .db3)
- CSV/TSV files
- Video files (coming soon)

Usage:
    from plexus.importers import RosbagImporter

    # Import a ROS bag
    importer = RosbagImporter("data.bag")
    schema = importer.detect_schema()

    for metrics in importer.iter_metrics():
        px.send_batch([(m.name, m.value, m.timestamp) for m in metrics])
"""

from plexus.importers.rosbag import RosbagImporter, RosbagSchema, TopicInfo

__all__ = [
    "RosbagImporter",
    "RosbagSchema",
    "TopicInfo",
]

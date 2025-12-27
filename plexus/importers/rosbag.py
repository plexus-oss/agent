"""
ROS Bag Importer - Import ROS1/ROS2 bags into Plexus

Supports:
- ROS1 bags (.bag)
- ROS2 bags (.db3)
- MCAP files (.mcap)

Usage:
    from plexus.importers import RosbagImporter

    importer = RosbagImporter("robot_data.bag")

    # Detect schema
    schema = importer.detect_schema()
    print(f"Found {len(schema.topics)} topics")

    # Iterate through metrics
    for batch in importer.iter_metrics(batch_size=100):
        for metric in batch:
            print(f"{metric.name}: {metric.value}")

    # Or upload directly to Plexus
    from plexus import Plexus
    px = Plexus()
    importer.upload_to_plexus(px, session_id="robot-test-001")
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Union

from plexus.adapters.base import Metric


# Image topic message types that contain video/image data
IMAGE_MESSAGE_TYPES = {
    "sensor_msgs/Image",
    "sensor_msgs/msg/Image",
    "sensor_msgs/CompressedImage",
    "sensor_msgs/msg/CompressedImage",
}

# Common numeric field types in ROS messages
NUMERIC_TYPES = {
    "float32", "float64", "double", "float",
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
    "bool",
}


@dataclass
class TopicInfo:
    """Information about a ROS topic"""
    name: str                           # Original ROS topic name (e.g., "/robot/imu")
    message_type: str                   # ROS message type (e.g., "sensor_msgs/Imu")
    message_count: int                  # Number of messages in bag
    plexus_name: str = ""               # Mapped Plexus metric name
    is_image: bool = False              # Whether this is an image/video topic
    fields: List[str] = field(default_factory=list)  # Extracted field names
    frequency_hz: float = 0.0           # Estimated publish frequency

    def __post_init__(self):
        # Convert ROS topic to Plexus metric name if not set
        if not self.plexus_name:
            # /robot/arm/joint_states → robot.arm.joint_states
            self.plexus_name = self.name.lstrip("/").replace("/", ".")

        # Detect if this is an image topic
        if not self.is_image:
            self.is_image = self.message_type in IMAGE_MESSAGE_TYPES


@dataclass
class RosbagSchema:
    """Schema extracted from a ROS bag"""
    topics: List[TopicInfo]
    duration_sec: float
    start_time: float
    end_time: float
    message_count: int
    bag_path: str
    bag_type: str  # "ros1", "ros2", "mcap"

    @property
    def telemetry_topics(self) -> List[TopicInfo]:
        """Topics that contain telemetry (non-image) data"""
        return [t for t in self.topics if not t.is_image]

    @property
    def image_topics(self) -> List[TopicInfo]:
        """Topics that contain image/video data"""
        return [t for t in self.topics if t.is_image]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "bag_path": self.bag_path,
            "bag_type": self.bag_type,
            "duration_sec": self.duration_sec,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "message_count": self.message_count,
            "topics": [
                {
                    "name": t.name,
                    "plexus_name": t.plexus_name,
                    "message_type": t.message_type,
                    "message_count": t.message_count,
                    "is_image": t.is_image,
                    "fields": t.fields,
                    "frequency_hz": t.frequency_hz,
                }
                for t in self.topics
            ],
        }


class RosbagImporter:
    """
    Import ROS bags into Plexus.

    Handles ROS1 bags, ROS2 bags (SQLite), and MCAP files.
    Automatically detects bag type and extracts schema.

    Args:
        bag_path: Path to the ROS bag file
        topics: Optional list of topics to import (default: all)
        skip_images: Whether to skip image topics (default: False)

    Example:
        importer = RosbagImporter("data.bag")
        schema = importer.detect_schema()

        for batch in importer.iter_metrics():
            for m in batch:
                px.send(m.name, m.value, timestamp=m.timestamp)
    """

    def __init__(
        self,
        bag_path: Union[str, Path],
        topics: Optional[List[str]] = None,
        skip_images: bool = False,
    ):
        self.bag_path = Path(bag_path)
        self.topics_filter = topics
        self.skip_images = skip_images

        if not self.bag_path.exists():
            raise FileNotFoundError(f"Bag file not found: {bag_path}")

        self._bag_type = self._detect_bag_type()
        self._schema: Optional[RosbagSchema] = None

        # Lazy imports
        self._reader = None
        self._connections = None

    def _detect_bag_type(self) -> str:
        """Detect the type of ROS bag"""
        suffix = self.bag_path.suffix.lower()

        if suffix == ".bag":
            return "ros1"
        elif suffix == ".mcap":
            return "mcap"
        elif suffix == ".db3":
            return "ros2"
        elif self.bag_path.is_dir():
            # ROS2 bags can be directories
            if (self.bag_path / "metadata.yaml").exists():
                return "ros2"

        # Try to detect by reading header
        try:
            with open(self.bag_path, "rb") as f:
                header = f.read(16)
                if header.startswith(b"#ROSBAG"):
                    return "ros1"
                elif header.startswith(b"\x89MCAP"):
                    return "mcap"
        except Exception:
            pass

        raise ValueError(f"Unknown bag format: {self.bag_path}")

    def _ensure_rosbags(self):
        """Ensure rosbags library is available"""
        try:
            import rosbags
            return rosbags
        except ImportError:
            raise ImportError(
                "ROS bag support requires the 'rosbags' library.\n"
                "Install it with: pip install plexus-agent[ros]"
            )

    def detect_schema(self) -> RosbagSchema:
        """
        Detect schema from the ROS bag.

        Reads bag metadata to extract topic information without
        reading all messages. Fast operation even for large bags.

        Returns:
            RosbagSchema with topic information
        """
        if self._schema is not None:
            return self._schema

        rosbags = self._ensure_rosbags()

        if self._bag_type == "ros1":
            schema = self._detect_schema_ros1(rosbags)
        elif self._bag_type == "ros2":
            schema = self._detect_schema_ros2(rosbags)
        elif self._bag_type == "mcap":
            schema = self._detect_schema_mcap(rosbags)
        else:
            raise ValueError(f"Unsupported bag type: {self._bag_type}")

        self._schema = schema
        return schema

    def _detect_schema_ros1(self, rosbags) -> RosbagSchema:
        """Detect schema from ROS1 bag"""
        from rosbags.rosbag1 import Reader

        topics = []
        start_time = float("inf")
        end_time = float("-inf")
        total_messages = 0

        with Reader(self.bag_path) as reader:
            for connection in reader.connections:
                # Filter topics if specified
                if self.topics_filter and connection.topic not in self.topics_filter:
                    continue

                topic_info = TopicInfo(
                    name=connection.topic,
                    message_type=connection.msgtype,
                    message_count=connection.msgcount,
                )

                # Skip images if requested
                if self.skip_images and topic_info.is_image:
                    continue

                topics.append(topic_info)
                total_messages += connection.msgcount

            # Get time range from first/last messages
            if reader.duration:
                start_time = reader.start_time / 1e9  # nanoseconds to seconds
                end_time = reader.end_time / 1e9
            else:
                start_time = 0
                end_time = 0

        # Calculate frequencies
        duration = end_time - start_time if end_time > start_time else 1.0
        for topic in topics:
            topic.frequency_hz = topic.message_count / duration

        return RosbagSchema(
            topics=topics,
            duration_sec=duration,
            start_time=start_time,
            end_time=end_time,
            message_count=total_messages,
            bag_path=str(self.bag_path),
            bag_type="ros1",
        )

    def _detect_schema_ros2(self, rosbags) -> RosbagSchema:
        """Detect schema from ROS2 bag"""
        from rosbags.rosbag2 import Reader

        topics = []
        start_time = float("inf")
        end_time = float("-inf")
        total_messages = 0

        with Reader(self.bag_path) as reader:
            for connection in reader.connections:
                # Filter topics if specified
                if self.topics_filter and connection.topic not in self.topics_filter:
                    continue

                topic_info = TopicInfo(
                    name=connection.topic,
                    message_type=connection.msgtype,
                    message_count=connection.msgcount,
                )

                # Skip images if requested
                if self.skip_images and topic_info.is_image:
                    continue

                topics.append(topic_info)
                total_messages += connection.msgcount

            # Get time range
            if reader.duration:
                start_time = reader.start_time / 1e9
                end_time = reader.end_time / 1e9
            else:
                start_time = 0
                end_time = 0

        duration = end_time - start_time if end_time > start_time else 1.0
        for topic in topics:
            topic.frequency_hz = topic.message_count / duration

        return RosbagSchema(
            topics=topics,
            duration_sec=duration,
            start_time=start_time,
            end_time=end_time,
            message_count=total_messages,
            bag_path=str(self.bag_path),
            bag_type="ros2",
        )

    def _detect_schema_mcap(self, rosbags) -> RosbagSchema:
        """Detect schema from MCAP file"""
        # MCAP uses the same rosbags.rosbag2 reader
        return self._detect_schema_ros2(rosbags)

    def iter_metrics(
        self,
        batch_size: int = 100,
        flatten_messages: bool = True,
    ) -> Generator[List[Metric], None, None]:
        """
        Iterate through messages as Plexus metrics.

        Yields batches of Metric objects converted from ROS messages.
        Large bags can be processed incrementally without loading
        everything into memory.

        Args:
            batch_size: Number of metrics per batch
            flatten_messages: If True, flatten nested message fields

        Yields:
            List of Metric objects

        Example:
            for batch in importer.iter_metrics(batch_size=100):
                for metric in batch:
                    px.send(metric.name, metric.value, timestamp=metric.timestamp)
        """
        rosbags = self._ensure_rosbags()

        if self._bag_type == "ros1":
            yield from self._iter_metrics_ros1(rosbags, batch_size, flatten_messages)
        elif self._bag_type in ("ros2", "mcap"):
            yield from self._iter_metrics_ros2(rosbags, batch_size, flatten_messages)
        else:
            raise ValueError(f"Unsupported bag type: {self._bag_type}")

    def _iter_metrics_ros1(
        self,
        rosbags,
        batch_size: int,
        flatten: bool,
    ) -> Generator[List[Metric], None, None]:
        """Iterate metrics from ROS1 bag"""
        from rosbags.rosbag1 import Reader
        from rosbags.serde import deserialize_cdr, ros1_to_cdr

        batch = []

        with Reader(self.bag_path) as reader:
            for connection, timestamp, rawdata in reader.messages():
                # Filter topics if specified
                if self.topics_filter and connection.topic not in self.topics_filter:
                    continue

                # Skip images if requested
                if self.skip_images and connection.msgtype in IMAGE_MESSAGE_TYPES:
                    continue

                # Convert timestamp (nanoseconds to seconds)
                ts = timestamp / 1e9

                # Convert ROS topic to Plexus name
                base_name = connection.topic.lstrip("/").replace("/", ".")

                try:
                    # Deserialize message
                    cdr_data = ros1_to_cdr(rawdata, connection.msgtype)
                    msg = deserialize_cdr(cdr_data, connection.msgtype)

                    # Convert message to metrics
                    metrics = self._message_to_metrics(msg, base_name, ts, flatten)
                    batch.extend(metrics)

                    if len(batch) >= batch_size:
                        yield batch
                        batch = []

                except Exception:
                    # Skip messages that fail to deserialize
                    continue

        # Yield remaining
        if batch:
            yield batch

    def _iter_metrics_ros2(
        self,
        rosbags,
        batch_size: int,
        flatten: bool,
    ) -> Generator[List[Metric], None, None]:
        """Iterate metrics from ROS2/MCAP bag"""
        from rosbags.rosbag2 import Reader
        from rosbags.serde import deserialize_cdr

        batch = []

        with Reader(self.bag_path) as reader:
            for connection, timestamp, rawdata in reader.messages():
                # Filter topics if specified
                if self.topics_filter and connection.topic not in self.topics_filter:
                    continue

                # Skip images if requested
                if self.skip_images and connection.msgtype in IMAGE_MESSAGE_TYPES:
                    continue

                # Convert timestamp (nanoseconds to seconds)
                ts = timestamp / 1e9

                # Convert ROS topic to Plexus name
                base_name = connection.topic.lstrip("/").replace("/", ".")

                try:
                    # Deserialize message
                    msg = deserialize_cdr(rawdata, connection.msgtype)

                    # Convert message to metrics
                    metrics = self._message_to_metrics(msg, base_name, ts, flatten)
                    batch.extend(metrics)

                    if len(batch) >= batch_size:
                        yield batch
                        batch = []

                except Exception:
                    # Skip messages that fail to deserialize
                    continue

        # Yield remaining
        if batch:
            yield batch

    def _message_to_metrics(
        self,
        msg: Any,
        base_name: str,
        timestamp: float,
        flatten: bool,
    ) -> List[Metric]:
        """
        Convert a ROS message to Plexus metrics.

        Flattens nested messages into dot-notation metric names.

        Examples:
            Imu message → [
                Metric("imu.linear_acceleration.x", 9.81),
                Metric("imu.linear_acceleration.y", 0.0),
                Metric("imu.angular_velocity.x", 0.01),
                ...
            ]

            JointState message → [
                Metric("joint_states.position", [0.1, 0.2, 0.3]),
                Metric("joint_states.velocity", [0.0, 0.0, 0.0]),
                ...
            ]
        """
        metrics = []

        if flatten:
            self._flatten_message(msg, base_name, timestamp, metrics)
        else:
            # Send entire message as dict
            msg_dict = self._message_to_dict(msg)
            metrics.append(Metric(base_name, msg_dict, timestamp=timestamp))

        return metrics

    def _flatten_message(
        self,
        obj: Any,
        prefix: str,
        timestamp: float,
        metrics: List[Metric],
    ) -> None:
        """Recursively flatten a message into metrics"""
        # Handle primitive types
        if isinstance(obj, (int, float, bool)):
            metrics.append(Metric(prefix, obj, timestamp=timestamp))
            return

        if isinstance(obj, str):
            metrics.append(Metric(prefix, obj, timestamp=timestamp))
            return

        # Handle numpy arrays (common in ROS)
        if hasattr(obj, "tolist"):
            # numpy array - convert to list and send as array metric
            metrics.append(Metric(prefix, obj.tolist(), timestamp=timestamp))
            return

        # Handle lists/tuples
        if isinstance(obj, (list, tuple)):
            # For short arrays of primitives, send as array
            if len(obj) <= 100 and all(isinstance(x, (int, float, bool)) for x in obj):
                metrics.append(Metric(prefix, list(obj), timestamp=timestamp))
            else:
                # For longer arrays, create indexed metrics
                for i, item in enumerate(obj):
                    self._flatten_message(item, f"{prefix}[{i}]", timestamp, metrics)
            return

        # Handle ROS message objects (have __slots__ or __dataclass_fields__)
        if hasattr(obj, "__slots__"):
            for slot in obj.__slots__:
                if slot.startswith("_"):
                    continue
                value = getattr(obj, slot, None)
                if value is not None:
                    self._flatten_message(value, f"{prefix}.{slot}", timestamp, metrics)
            return

        if hasattr(obj, "__dataclass_fields__"):
            for field_name in obj.__dataclass_fields__:
                if field_name.startswith("_"):
                    continue
                value = getattr(obj, field_name, None)
                if value is not None:
                    self._flatten_message(value, f"{prefix}.{field_name}", timestamp, metrics)
            return

        # Handle dicts
        if isinstance(obj, dict):
            for key, value in obj.items():
                self._flatten_message(value, f"{prefix}.{key}", timestamp, metrics)
            return

        # Unknown type - try to send as string
        try:
            metrics.append(Metric(prefix, str(obj), timestamp=timestamp))
        except Exception:
            pass

    def _message_to_dict(self, obj: Any) -> Any:
        """Convert ROS message to dictionary"""
        if isinstance(obj, (int, float, bool, str)):
            return obj

        if hasattr(obj, "tolist"):
            return obj.tolist()

        if isinstance(obj, (list, tuple)):
            return [self._message_to_dict(x) for x in obj]

        if hasattr(obj, "__slots__"):
            return {
                slot: self._message_to_dict(getattr(obj, slot, None))
                for slot in obj.__slots__
                if not slot.startswith("_")
            }

        if hasattr(obj, "__dataclass_fields__"):
            return {
                name: self._message_to_dict(getattr(obj, name, None))
                for name in obj.__dataclass_fields__
                if not name.startswith("_")
            }

        if isinstance(obj, dict):
            return {k: self._message_to_dict(v) for k, v in obj.items()}

        return str(obj)

    def upload_to_plexus(
        self,
        client,  # Plexus client
        session_id: Optional[str] = None,
        batch_size: int = 100,
        progress_callback: Optional[callable] = None,
    ) -> Dict[str, Any]:
        """
        Upload bag contents directly to Plexus.

        Args:
            client: Plexus client instance
            session_id: Optional session ID for grouping data
            batch_size: Number of points per API call
            progress_callback: Optional callback(uploaded, total) for progress

        Returns:
            Dict with upload statistics

        Example:
            from plexus import Plexus
            px = Plexus()
            importer = RosbagImporter("data.bag")
            stats = importer.upload_to_plexus(px, session_id="test-001")
            print(f"Uploaded {stats['metrics_uploaded']} metrics")
        """
        schema = self.detect_schema()
        total_messages = schema.message_count
        uploaded = 0
        errors = 0

        # Use session context if provided
        from contextlib import nullcontext

        context = client.session(session_id) if session_id else nullcontext()

        with context:
            for batch in self.iter_metrics(batch_size=batch_size):
                try:
                    for metric in batch:
                        client.send(
                            metric.name,
                            metric.value,
                            timestamp=metric.timestamp,
                            tags=metric.tags,
                        )
                    uploaded += len(batch)

                    if progress_callback:
                        progress_callback(uploaded, total_messages)

                except Exception:
                    errors += 1

        return {
            "bag_path": str(self.bag_path),
            "bag_type": self._bag_type,
            "session_id": session_id,
            "metrics_uploaded": uploaded,
            "errors": errors,
            "duration_sec": schema.duration_sec,
            "topics_imported": len(schema.telemetry_topics),
        }

    def extract_images(
        self,
        output_dir: Union[str, Path],
        topics: Optional[List[str]] = None,
        frame_rate: Optional[float] = None,
        format: str = "jpg",
    ) -> Dict[str, Any]:
        """
        Extract image frames from the bag.

        Args:
            output_dir: Directory to save extracted frames
            topics: Image topics to extract (default: all)
            frame_rate: Target frame rate (None = extract all frames)
            format: Output format (jpg, png)

        Returns:
            Dict with extraction statistics

        Example:
            stats = importer.extract_images(
                "./frames",
                frame_rate=10.0,  # 10 fps
            )
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            raise ImportError(
                "Image extraction requires OpenCV.\n"
                "Install it with: pip install plexus-agent[video]"
            )

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        schema = self.detect_schema()
        image_topics = [t for t in schema.image_topics if not topics or t.name in topics]

        if not image_topics:
            return {"error": "No image topics found", "frames_extracted": 0}

        self._ensure_rosbags()
        from rosbags.rosbag2 import Reader
        from rosbags.rosbag1 import Reader as Reader1
        from rosbags.serde import deserialize_cdr

        # Track last frame time per topic for frame rate limiting
        last_frame_time: Dict[str, float] = {}
        frame_interval = 1.0 / frame_rate if frame_rate else 0

        frames_extracted = 0
        frame_counts: Dict[str, int] = {}

        ReaderClass = Reader1 if self._bag_type == "ros1" else Reader

        with ReaderClass(self.bag_path) as reader:
            for connection, timestamp, rawdata in reader.messages():
                if connection.topic not in [t.name for t in image_topics]:
                    continue

                ts = timestamp / 1e9

                # Frame rate limiting
                if frame_rate:
                    last_ts = last_frame_time.get(connection.topic, 0)
                    if ts - last_ts < frame_interval:
                        continue

                try:
                    # Deserialize based on bag type
                    if self._bag_type == "ros1":
                        from rosbags.serde import ros1_to_cdr
                        cdr_data = ros1_to_cdr(rawdata, connection.msgtype)
                        msg = deserialize_cdr(cdr_data, connection.msgtype)
                    else:
                        msg = deserialize_cdr(rawdata, connection.msgtype)

                    # Extract image data
                    if "Compressed" in connection.msgtype:
                        # CompressedImage
                        img_data = np.frombuffer(msg.data, np.uint8)
                        frame = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
                    else:
                        # Raw Image
                        if msg.encoding in ("rgb8", "bgr8"):
                            frame = np.frombuffer(msg.data, np.uint8)
                            frame = frame.reshape((msg.height, msg.width, 3))
                            if msg.encoding == "rgb8":
                                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        elif msg.encoding == "mono8":
                            frame = np.frombuffer(msg.data, np.uint8)
                            frame = frame.reshape((msg.height, msg.width))
                        else:
                            continue  # Skip unsupported encodings

                    if frame is None:
                        continue

                    # Save frame
                    topic_name = connection.topic.lstrip("/").replace("/", "_")
                    frame_num = frame_counts.get(connection.topic, 0)
                    frame_counts[connection.topic] = frame_num + 1

                    filename = f"{topic_name}_{frame_num:06d}.{format}"
                    cv2.imwrite(str(output_path / filename), frame)

                    frames_extracted += 1
                    last_frame_time[connection.topic] = ts

                except Exception:
                    continue

        return {
            "output_dir": str(output_path),
            "frames_extracted": frames_extracted,
            "frame_counts": frame_counts,
            "topics": [t.name for t in image_topics],
        }


# Convenience function for CLI
def import_rosbag(
    bag_path: str,
    session_id: Optional[str] = None,
    topics: Optional[List[str]] = None,
    dry_run: bool = False,
    extract_video: bool = False,
    video_output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Import a ROS bag into Plexus.

    Convenience function for CLI usage.

    Args:
        bag_path: Path to the ROS bag
        session_id: Optional session ID
        topics: Topics to import (default: all)
        dry_run: If True, only detect schema without importing
        extract_video: If True, extract video frames
        video_output_dir: Directory for video frames

    Returns:
        Dict with import results
    """
    importer = RosbagImporter(bag_path, topics=topics)
    schema = importer.detect_schema()

    result = {
        "schema": schema.to_dict(),
        "dry_run": dry_run,
    }

    if dry_run:
        return result

    # Upload telemetry
    from plexus.client import Plexus
    px = Plexus()

    upload_stats = importer.upload_to_plexus(px, session_id=session_id)
    result["upload"] = upload_stats

    # Extract video if requested
    if extract_video and schema.image_topics:
        video_dir = video_output_dir or f"./frames_{Path(bag_path).stem}"
        video_stats = importer.extract_images(video_dir)
        result["video"] = video_stats

    return result

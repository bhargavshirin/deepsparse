# Copyright (c) 2021 - present / Neuralmagic, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Benchmarking script for ONNX models with the DeepSparse engine.

##########
Command help:
usage: deepsparse.benchmark [-h] [-b BATCH_SIZE] [-shapes INPUT_SHAPES]
                            [-ncores NUM_CORES] [-s {async,sync,elastic}]
                            [-t TIME] [-w WARMUP_TIME] [-nstreams NUM_STREAMS]
                            [-pin {none,core,numa}]
                            [-e {deepsparse,onnxruntime}] [-q]
                            [-x EXPORT_PATH]
                            model_path

Benchmark ONNX models in the DeepSparse Engine

positional arguments:
  model_path            Path to an ONNX model file or SparseZoo model stub.

optional arguments:
  -h, --help            show this help message and exit.
  -b BATCH_SIZE, --batch_size BATCH_SIZE
                        The batch size to run the analysis for. Must be
                        greater than 0.
  -shapes INPUT_SHAPES, --input_shapes INPUT_SHAPES
                        Override the shapes of the inputs, i.e. -shapes
                        "[1,2,3],[4,5,6],[7,8,9]" results in input0=[1,2,3]
                        input1=[4,5,6] input2=[7,8,9].
  -ncores NUM_CORES, --num_cores NUM_CORES
                        The number of physical cores to run the analysis on,
                        defaults to all physical cores available on the system.
  -s {async,sync,elastic}, --scenario {async,sync,elastic}
                        Choose between using the async, sync and elastic
                        scenarios. Sync and async are similar to the single-
                        stream/multi-stream scenarios. Elastic is a newer
                        scenario that behaves similarly to the async scenario
                        but uses a different scheduling backend. Default value
                        is async.
  -t TIME, --time TIME  The number of seconds the benchmark will run. Default
                        is 10 seconds.
  -w WARMUP_TIME, --warmup_time WARMUP_TIME
                        The number of seconds the benchmark will warmup before
                        running.Default is 2 seconds.
  -nstreams NUM_STREAMS, --num_streams NUM_STREAMS
                        The number of streams that will submit inferences in
                        parallel using async scenario. Default is
                        automatically determined for given hardware and may be
                        sub-optimal.
  -pin {none,core,numa}, --thread_pinning {none,core,numa}
                        Enable binding threads to cores ('core' the default),
                        threads to cores on sockets ('numa'), or disable
                        ('none').
  -e {deepsparse,onnxruntime}, --engine {deepsparse,onnxruntime}
                        Inference engine backend to run eval on. Choices are
                        'deepsparse', 'onnxruntime'. Default is 'deepsparse'.
  -q, --quiet           Lower logging verbosity.
  -x EXPORT_PATH, --export_path EXPORT_PATH
                        Store results into a JSON file.

##########
Example on a BERT from SparseZoo:
deepsparse.benchmark \
   zoo:nlp/question_answering/bert-base/pytorch/huggingface/squad/base-none

##########
Example on a BERT from SparseZoo with sequence length 512:
deepsparse.benchmark \
   zoo:nlp/question_answering/bert-base/pytorch/huggingface/squad/base-none \
   --input_shapes "[1,512],[1,512],[1,512]"

##########
Example on local ONNX model:
deepsparse.benchmark /PATH/TO/model.onnx

##########
Example on local ONNX model at batch size 32 with synchronous (singlestream) execution:
deepsparse.benchmark /PATH/TO/model.onnx --batch_size 32 --scenario sync

"""

import argparse
import json
import logging
import os

from deepsparse import Scheduler, compile_model
from deepsparse.benchmark_model.ort_engine import ORTEngine
from deepsparse.benchmark_model.stream_benchmark import model_stream_benchmark
from deepsparse.log import set_logging_level
from deepsparse.utils import (
    generate_random_inputs,
    model_to_path,
    override_onnx_input_shapes,
    parse_input_shapes,
)


_LOGGER = logging.getLogger(__name__)

DEEPSPARSE_ENGINE = "deepsparse"
ORT_ENGINE = "onnxruntime"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark ONNX models in the DeepSparse Engine"
    )

    parser.add_argument(
        "model_path",
        type=str,
        help="Path to an ONNX model file or SparseZoo model stub",
    )

    parser.add_argument(
        "-b",
        "--batch_size",
        type=int,
        default=1,
        help="The batch size to run the analysis for. Must be greater than 0",
    )
    parser.add_argument(
        "-i",
        "-shapes",
        "--input_shapes",
        type=str,
        default="",
        help="Override the shapes of the inputs, "
        'i.e. -shapes "[1,2,3],[4,5,6],[7,8,9]" results in '
        "input0=[1,2,3] input1=[4,5,6] input2=[7,8,9]",
    )
    parser.add_argument(
        "-ncores",
        "--num_cores",
        type=int,
        default=None,
        help=(
            "The number of physical cores to run the analysis on, "
            "defaults to all physical cores available on the system"
        ),
    )
    parser.add_argument(
        "-s",
        "--scenario",
        type=str,
        default="async",
        choices=["async", "sync", "elastic"],
        help=(
            "Choose between using the async, sync and elastic scenarios. Sync and "
            "async are similar to the single-stream/multi-stream scenarios. Elastic "
            "is a newer scenario that behaves similarly to the async scenario "
            "but uses a different scheduling backend. Default value is async."
        ),
    )
    parser.add_argument(
        "-t",
        "--time",
        type=int,
        default=10,
        help="The number of seconds the benchmark will run. Default is 10 seconds.",
    )
    parser.add_argument(
        "-w",
        "--warmup_time",
        type=int,
        default=2,
        help=(
            "The number of seconds the benchmark will warmup before running."
            "Default is 2 seconds."
        ),
    )
    parser.add_argument(
        "-nstreams",
        "--num_streams",
        type=int,
        default=None,
        help=(
            "The number of streams that will submit inferences in parallel using "
            "async scenario. Default is automatically determined for given hardware "
            "and may be sub-optimal."
        ),
    )
    parser.add_argument(
        "-pin",
        "--thread_pinning",
        type=str,
        default="core",
        choices=["none", "core", "numa"],
        help=(
            "Enable binding threads to cores ('core' the default), "
            "threads to cores on sockets ('numa'), or disable ('none')"
        ),
    )
    parser.add_argument(
        "-e",
        "--engine",
        type=str,
        default=DEEPSPARSE_ENGINE,
        choices=[DEEPSPARSE_ENGINE, ORT_ENGINE],
        help=(
            "Inference engine backend to run eval on. Choices are 'deepsparse', "
            "'onnxruntime'. Default is 'deepsparse'"
        ),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        help="Lower logging verbosity",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "-x",
        "--export_path",
        help="Store results into a JSON file",
        type=str,
        default=None,
    )

    return parser.parse_args()


def decide_thread_pinning(pinning_mode: str):
    pinning_mode = pinning_mode.lower()

    if pinning_mode in "core":
        os.environ["NM_BIND_THREADS_TO_CORES"] = "1"
        _LOGGER.info("Thread pinning to cores enabled")
    elif pinning_mode in "numa":
        os.environ["NM_BIND_THREADS_TO_CORES"] = "0"
        os.environ["NM_BIND_THREADS_TO_SOCKETS"] = "1"
        _LOGGER.info("Thread pinning to socket/numa nodes enabled")
    elif pinning_mode in "none":
        os.environ["NM_BIND_THREADS_TO_CORES"] = "0"
        os.environ["NM_BIND_THREADS_TO_SOCKETS"] = "0"
        _LOGGER.info("Thread pinning disabled, performance may be sub-optimal")
    else:
        _LOGGER.info(
            "Recieved invalid option for thread_pinning '{}', skipping".format(
                pinning_mode
            )
        )


def parse_scheduler(scenario):
    if scenario == "multistream":
        return Scheduler.multi_stream
    elif scenario == "singlestream":
        return Scheduler.single_stream
    elif scenario == "elastic":
        return Scheduler.elastic
    else:
        return Scheduler.multi_stream


def parse_scenario(scenario):
    if scenario == "async":
        return "multistream"
    elif scenario == "sync":
        return "singlestream"
    elif scenario == "elastic":
        return "elastic"
    else:
        _LOGGER.info(
            "Recieved invalid option for scenario'{}', defaulting to async".format(
                scenario
            )
        )
        return "multistream"


def main():

    args = parse_args()

    if args.quiet:
        set_logging_level(logging.WARN)

    decide_thread_pinning(args.thread_pinning)

    scenario = parse_scenario(args.scenario.lower())
    scheduler = parse_scheduler(scenario)
    input_shapes = parse_input_shapes(args.input_shapes)

    orig_model_path = args.model_path
    args.model_path = model_to_path(args.model_path)

    # Compile the ONNX into a runnable model
    if args.engine == DEEPSPARSE_ENGINE:
        model = compile_model(
            model=args.model_path,
            batch_size=args.batch_size,
            num_cores=args.num_cores,
            scheduler=scheduler,
            input_shapes=input_shapes,
        )
    elif args.engine == ORT_ENGINE:
        model = ORTEngine(
            model=args.model_path,
            batch_size=args.batch_size,
            num_cores=args.num_cores,
            input_shapes=input_shapes,
        )
    _LOGGER.info(model)

    # Generate random inputs to feed the model
    # TODO(mgoin): should be able to query Engine class instead of loading ONNX
    if input_shapes:
        with override_onnx_input_shapes(args.model_path, input_shapes) as model_path:
            input_list = generate_random_inputs(model_path, args.batch_size)
    else:
        input_list = generate_random_inputs(args.model_path, args.batch_size)

    if args.num_streams:
        _LOGGER.info("num_streams set to {}".format(args.num_streams))
    elif not args.num_streams and scenario not in "singlestream":
        # If num_streams isn't defined, find a default
        args.num_streams = max(1, int(model.num_cores / 2))
        _LOGGER.info(
            "num_streams default value chosen of {}. "
            "This requires tuning and may be sub-optimal".format(args.num_streams)
        )

    # Benchmark
    _LOGGER.info(
        "Starting '{}' performance measurements for {} seconds".format(
            args.scenario, args.time
        )
    )
    benchmark_result = model_stream_benchmark(
        model,
        input_list,
        scenario=scenario,
        seconds_to_run=args.time,
        seconds_to_warmup=args.warmup_time,
        num_streams=args.num_streams,
    )

    # Results summary
    print("Original Model Path: {}".format(orig_model_path))
    print("Batch Size: {}".format(args.batch_size))
    print("Scenario: {}".format(scenario))
    print("Throughput (items/sec): {:.4f}".format(benchmark_result["items_per_sec"]))
    print("Latency Mean (ms/batch): {:.4f}".format(benchmark_result["mean"]))
    print("Latency Median (ms/batch): {:.4f}".format(benchmark_result["median"]))
    print("Latency Std (ms/batch): {:.4f}".format(benchmark_result["std"]))
    print("Iterations: {}".format(int(benchmark_result["iterations"])))

    if args.export_path:
        # Export results
        print("Saving benchmark results to JSON file at {}".format(args.export_path))
        export_dict = {
            "engine": str(model),
            "orig_model_path": orig_model_path,
            "model_path": args.model_path,
            "batch_size": args.batch_size,
            "input_shapes": args.input_shapes,
            "num_cores": args.num_cores,
            "scenario": args.scenario,
            "scheduler": str(model.scheduler),
            "seconds_to_run": args.time,
            "num_streams": args.num_streams,
            "benchmark_result": benchmark_result,
        }
        with open(args.export_path, "w") as out:
            json.dump(export_dict, out, indent=2)


if __name__ == "__main__":
    main()
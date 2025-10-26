# Louvain
## General
Git repository for understanding modularity and [Louvain method](https://iopscience.iop.org/article/10.1088/1742-5468/2008/10/P10008/meta).

## Version History
| Version | Date  | Commit | Notes |
| :-----: | :-: | :-: | :-: |
| 0.0 | April 12, 2021 | [7659810](https://github.com/TeraokaKanekoLab/Louvain/commit/7659810596ec8fe5948b348248d064a4819eda45) | first commit |
| 0.1 | April 12, 2021 | [c59c3e7](https://github.com/TeraokaKanekoLab/Louvain/commit/c59c3e7970defa79c3a035842f22f6f6da8ec7e0) | Define Graph class |
| 0.2 | April 12, 2021 | [3b1c2ba](https://github.com/TeraokaKanekoLab/Louvain/commit/3b1c2baafef4cdbb1f4f6123dea035d472898e85) | Modularity Computation |
| 0.3 | April 13, 2021 | [4629e33](https://github.com/TeraokaKanekoLab/Louvain/commit/4629e3320bfe7ae768b0f3f61116ee307c5943b7) | community graph (weighted graph) class |
| 0.4 | April 13, 2021 | [812d3f1](https://github.com/TeraokaKanekoLab/Louvain/commit/812d3f1f0c908d5c9fc5686db1f83a6117f41a69) | Louvain method Completed? |
| 0.5 | April 15, 2021 | [cebe22d](https://github.com/TeraokaKanekoLab/Louvain/commit/cebe22dbe515b2ce28a9142a0d6dd41f1221fc97) | Louvain method Completed? |
| 0.6 | April 16, 2021 | [ee479cc](https://github.com/TeraokaKanekoLab/Louvain/commit/ee479cc5f1aba0528401c5517992b2f1e59fd3ff) | computation time drastically reduced |
| 0.7 | April 17, 2021 | [04879e3](https://github.com/TeraokaKanekoLab/Louvain/commit/04879e3cde5988c97afab50e5fe457e4b38c56ce) | Great Speedup |
| 0.8 | April 17, 2021 | [f4df9ff](https://github.com/TeraokaKanekoLab/Louvain/commit/f4df9ff5735e90f6b6a69a85916a6ff2b22a7beb) | Copying other's implementation |
| 0.9 | April 18, 2021 | [b11f460](https://github.com/TeraokaKanekoLab/Louvain/commit/b11f4608b5cecd1ae36400711caa6de872d5ef5a) | Louvain Method Completed |
| 0.10 | April 18, 2021 | [1ceb79b](https://github.com/TeraokaKanekoLab/Louvain/commit/1ceb79bc6a6b624395f5f478780ec06e7da43296) | Louvain Rewrite to non-iterate version |
| 0.11 | April 19, 2021 | [7efa1f0](https://github.com/TeraokaKanekoLab/Louvain/commit/7efa1f02950d64a81fdd5ab7c3c0cc00355b1a38) | Old files modified |
| 1.0 | April 20, 2021 | [83b19b7](https://github.com/TeraokaKanekoLab/Louvain/commit/83b19b7a08e55694de31ca7ec67488dc90ce4525) | Completed |

## Graph Data File Format
The graph data files need to follow the rule below. `<endpoint n>` needs to be an int (node id)

```
<endpoint 1> <endpoint 2>
<endpoint 3> <endpoint 4>
.
.
.
```

Let's say there is a graph like this.
![](https://i.ibb.co/g6F8pfv/images-dragged.jpg)

The following data (`graph/simple_graph.gr`) represents this simple graph with 9 nodes and 12 edges, which are <0, 1>, ..., <7, 8>.

```
0 1
0 2
1 2
1 3
2 3
2 7
3 4
3 5
4 6
5 6
5 8
7 8
```

## How to Run the Program
Try the following command to get an instant result.

```
sh run.sh all graph/simple_graph.gr
```

## References
1. Blondel, Vincent D; Guillaume, Jean-Loup; Lambiotte, Renaud; Lefebvre, Etienne (9 October 2008). [Fast unfolding of communities in large networks](https://iopscience.iop.org/article/10.1088/1742-5468/2008/10/P10008/meta). Journal of Statistical Mechanics: Theory and Experiment. 2008 (10): P10008.

#include "graph.cpp"

int main()
{
    Graph g("graph/cmu.gr");
    g.print_links();
    g.print_degrees();
    return 0;
}
modularity() {
    echo "g++ src/louvain.cpp -o ./louvain --std=c++17"
    g++ src/louvain.cpp -o ./louvain --std=c++17
    echo "./louvain $1 $2"
    ./louvain $1 $2
    rm ./louvain
}
old() {
    echo "g++ old/main.cpp -o ./main --std=c++17"
    g++ old/main.cpp -o ./main --std=c++17
    echo "./main $1 $2"
    ./main $1 $2
    rm ./main
}

case $1 in
"all")
    modularity $2 $3
    ;;
"old")
    old $2 $3
    ;;
esac

#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include <kysdk-vector-engine-client/Database.h>
#include <kysdk-vector-engine-client/types/Constants.h>
#include <kysdk-vector-engine-client/types/FieldData.h>

using namespace VectorDB;

static int check(const char* step, const Status& status) {
    std::cout << step << ": code=" << static_cast<int>(status.Code())
              << " ok=" << (status.IsOk() ? "true" : "false")
              << " msg=" << status.Message() << std::endl;
    return status.IsOk() ? 0 : 1;
}

int main() {
    const std::string collection = "memguard_probe_cpp";
    const int dim = 8;

    auto db = Database::Create();
    if (!db) {
        std::cerr << "create db failed" << std::endl;
        return 2;
    }

    Status status = db->Connect();
    if (check("connect", status)) {
        return 3;
    }

    bool has_collection = false;
    status = db->HasCollection(collection, has_collection);
    check("has_before", status);
    if (has_collection) {
        status = db->DropCollection(collection);
        check("drop_existing", status);
    }

    status = db->CreateCollection(collection, dim, false, false);
    if (check("create", status)) {
        return 4;
    }

    std::vector<FieldDataPtr> fields;
    auto ids = std::make_shared<Int64FieldData>(DEFAULT_ID_FIELD_NAME);
    ids->Add(static_cast<int64_t>(1));
    ids->Add(static_cast<int64_t>(2));

    auto vectors = std::make_shared<FloatVecFieldData>(DEFAULT_VECTOR_FIELD_NAME);
    vectors->Add(std::vector<float>{1, 0, 0, 0, 0, 0, 0, 0});
    vectors->Add(std::vector<float>{0, 1, 0, 0, 0, 0, 0, 0});
    fields.push_back(ids);
    fields.push_back(vectors);

    DmlResults dml;
    status = db->Insert(collection, fields, dml);
    if (check("insert", status)) {
        return 5;
    }

    status = db->LoadCollection(collection);
    check("load", status);

    SearchArguments args(collection, 2, MetricType::COSINE);
    status = args.AddTargetVector(DEFAULT_VECTOR_FIELD_NAME, std::vector<float>{1, 0, 0, 0, 0, 0, 0, 0});
    if (check("add_vector", status)) {
        return 6;
    }

    SearchResults results;
    status = db->Search(args, results, 5000);
    if (check("search", status)) {
        return 7;
    }

    for (auto& single : results.Results()) {
        const auto& id_array = single.Ids();
        const auto& scores = single.Scores();
        if (!id_array.IsIntegerID()) {
            continue;
        }
        const auto& ids_result = id_array.IntIDArray();
        for (size_t idx = 0; idx < ids_result.size(); ++idx) {
            float score = idx < scores.size() ? scores[idx] : -1.0f;
            std::cout << "hit id=" << ids_result[idx] << " score=" << score << std::endl;
        }
    }

    status = db->DropCollection(collection);
    check("cleanup_drop", status);
    db->Disconnect();
    return 0;
}

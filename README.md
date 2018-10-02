# BB2CodeBuild

> As of October 2018, support for BitBucket webhooks is now built-in to AWS CodeBuild,
> making this project obsolete. See: [AWS Announcement](https://forums.aws.amazon.com/ann.jspa?annID=6177)

## Summary

This project provides an AWS Lambda function that receives webhook calls from
Bitbucket Cloud and launches AWS CodeBuild jobs in response.

When a webhook notification is received, BB2CodeBuild queries the CodeBuild API
to find a project named `username-reponame-branch` in your AWS account,
corresponding to the repository and branch that was pushed. If one is found, a build job is
started for the matching CodeBuild project.

For example, a webhook call about a push to the `pheb/example` repository
`master` branch could result in the the following activity:

```text
[INFO] 2017-08-14T18:01:20.239Z Received webhook notification for pheb/example:
[INFO] 2017-08-14T18:01:20.239Z --branch master to 12345678fc512a7a9b9e7c1160a5cabc2b0a73d9
[INFO] 2017-08-14T18:01:20.475Z Starting CodeBuild project pheb-example-master:
[INFO] 2017-08-14T18:01:20.660Z --Build ID = pheb-example-master:90abcdef-4096-4e60-83da-ebcd1cc3127f
REPORT   Duration: 422.40 ms   Billed Duration: 500 ms   Memory Size: 128 MB   Max Memory Used: 28 MB
```

This integration provides an inexpensive way to perform automated builds for low
volume or personal projects. At low volumes, you could operate almost entirely
within the perpetual AWS free tier. (You might spend a couple of cents on API
Gateway or S3, but as of the time this was written, there is a free allotment of
both Lambda and CodeBuild time every month.)

## Linking CodeBuild projects

To simplify configuration, BB2CodeBuild requires that CodeBuild projects follow
a specific naming pattern: `username-reponame-branch`, with the three elements
of the project name separated by a single hyphen.

CodeBuild [constrains][chars] project names to the character set
`[-_A-Za-z0-9]`. Any other characters that would otherwise be part of the
project name must be replaced with `_`. For example, to link a CodeBuild project
to a branch named `bug#1234` in the `pheb/example` repository, name the
CodeBuild project `pheb-example-bug_1234`.

Your CodeBuild projects must specify Bitbucket as the source for the build. It
is sufficient to point to the root of the repository, and not a particular
branch; BB2CodeBuild will send CodeBuild the specific commit ID to build when
starting a CodeBuild job.

## Installation

This project uses the [serverless framework][sf] to manage deployment. Before
deploying this project, you must [install their framework][sfinst] on your
development machine and [set up your AWS credentials][sfcreds]. Then:

* Clone this repository. If your CodeBuild projects are in an AWS region other
  than `us-east-1`, edit `serverless.yml` to specify the region.

* Run `serverless deploy` to deploy the service. Once deployment completes,
  serverless will display the endpoint URL of your webhook, which looks like
  this:
  `https://abc12xyz89.execute-api.us-east-1.amazonaws.com/dev/bb2cb_webhook`.

* In the Bitbucket web interface, go to the Settings page for a repository you
  want to link to CodeBuild, and create a webhook using your endpoint URL. The
  default settings, which trigger the webhook on push events, are correct. (If
  you are new to CodeBuild, you can build [pheb/example][phebex] to try it out.)

That's it! Once the webhook is configured, a push to Bitbucket should start up
any associated CodeBuild jobs automatically.

A single installed instance of the BB2CodeBuild webhook can be linked to
multiple repositories in Bitbucket. The only limitation is that the Lambda
function and CodeBuild projects must exist in the same AWS region.

## Troubleshooting

There are three places you can check logs to troubleshoot issues in the integration or
with your build:

* Bitbucket lets you view webhook requests in its UI. This lets you see what got
  sent and received, and what HTTP status code came back from API Gateway/Lambda
  for a given webhook notification.

* CloudWatch Logs will have a log group for BB2CodeBuild. Any errors related to
  authentication to Bitbucket or AWS permissions will appear here, usually 10 to
  15 seconds after the Lambda function is invoked. If the Lambda function is not
  starting, you can turn on verbose logs for API Gateway to troubleshoot; but
  like everything related to API Gateway, figuring out how to do so is
  [overly][ugh] [complicated][oof].

* CodeBuild creates another log group for your build job, logging each build
  step.

## Uninstalling

To remove BB2CodeBuild from your AWS account, along with the associated AWS
resources that make it work, run `serverless remove`.

## Security considerations

Your webhook function is open to the world, as Bitbucket does not currently
provide an authentication mechanism for webhook calls. Although the API Gateway
URL is obscure, a malicious outsider could potentially cause you to consume AWS
resources if they discovered the URL and could POST convincing payloads.

To add a "password" to the webhook call, uncomment the `token` line in
`serverless.yml` and enter a string with some entropy. Add it to the query
string of the webhook URL in Bitbucket as `?token=value`. If a `token` is
configured in `serverless.yml`, calls to the webhook without the correct token
will fail with a 403-Forbidden error.

## Advanced functionality

To create build jobs on tags as well as branches, create a CodeBuild project
using `all_tags` as the branch name (for example, `pheb-example-all_tags`). Use
the placeholder `(tag)` in the **Artifacts name** setting of the CodeBuild
project definition, and the tag name will be used when uploading the build
artifact to S3. (Apologies for the weird placeholder with parentheses, but the
AWS Console is picky about what characters are allowed in build artifact
filenames.)

If you really dislike the `username-reponame-branch` convention, you can specify
your own convention by specifying it in `serverless.yml`. All three parameters
are required:

```yaml
  environment:
    # you can customize this if you don't like the default naming convention for CodeBuild projects
    pattern: "$username-$reponame-$branch"
```

BB2CodeBuild passes two environment variables from Bitbucket to the CodeBuild
build job. These are usable from your build script.

| Env. Variable | Example Value                            |
| ------------- |------------------------------------------|
| GIT_COMMIT    | 12345678fc512a7a9b9e7c1160a5cabc2b0a73d9 |
| GIT_BRANCH    | master                                   |

## Version history

* 1.0 - (14-Aug-2017) - initial version

[example]: https://bitbucket.org/pheb/example
[chars]: http://docs.aws.amazon.com/codebuild/latest/userguide/limits.html
[sf]: https://serverless.com
[sfinst]: https://serverless.com/framework/docs/providers/aws/guide/installation/
[sfcreds]: https://serverless.com/framework/docs/providers/aws/guide/credentials/
[phebex]: https://bitbucket.org/pheb/example
[ugh]: https://aws.amazon.com/premiumsupport/knowledge-center/api-gateway-cloudwatch-logs/
[oof]: http://docs.aws.amazon.com/apigateway/latest/developerguide/stages.html#how-to-stage-settings